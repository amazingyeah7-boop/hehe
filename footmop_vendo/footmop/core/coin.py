"""Coin acceptor pulse decoding.

Raw edges (from a GPIO alert callback, with kernel timestamps) go into a
queue; a single worker thread runs the pure ``CoinDecoder`` so decoding is
never done on the GPIO library's callback thread and never on the GUI thread.

Filtering layers, from the outside in:
  1. Kernel/lgpio glitch filter (``coin.glitch_filter_us``) drops sub-ms spikes.
  2. Pulse width must be within [pulse_min_ms, pulse_max_ms].
  3. Gap since the previous valid pulse must be >= min_gap_ms (contact bounce).
  4. If pulses_per_coin > 1, pulses are grouped into bursts that end after
     burst_settle_ms of silence; leftovers that don't make a full coin are
     rejected and logged.
  5. A line held active for stuck_active_s is reported as a wiring fault.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from ..config import CoinCfg

log = logging.getLogger("footmop.coin")

CoinCallback = Callable[[int, int], None]        # (value_pesos, pulses)
RejectCallback = Callable[[str], None]           # (reason)


class CoinDecoder:
    def __init__(self, cfg: CoinCfg, on_coin: CoinCallback, on_reject: RejectCallback):
        self.cfg = cfg
        self.on_coin = on_coin
        self.on_reject = on_reject
        self.active_level = 0 if cfg.active_low else 1
        self._pulse_start_ns: Optional[int] = None
        self._pulse_start_mono: Optional[float] = None
        self._last_valid_end_ns: Optional[int] = None
        self._burst = 0
        self._burst_last_mono = 0.0
        self._stuck_reported = False
        self.accepted = 0
        self.rejected = 0

    # ------------------------------------------------------------------ edges
    def edge(self, level: int, t_ns: int, now_mono: float) -> None:
        if level == self.active_level:
            if self._pulse_start_ns is not None:
                # Two active edges in a row: we missed a release. Restart.
                self._reject("missed release edge")
            self._pulse_start_ns = t_ns
            self._pulse_start_mono = now_mono
            return

        # idle edge = end of pulse
        if self._pulse_start_ns is None:
            return  # idle->idle (e.g. first edge after boot); ignore
        start_ns = self._pulse_start_ns
        self._pulse_start_ns = None
        self._pulse_start_mono = None
        if self._stuck_reported:
            self._stuck_reported = False
            log.warning("coin line released after being stuck")
            return
        width_ms = (t_ns - start_ns) / 1e6
        if width_ms < self.cfg.pulse_min_ms:
            self._reject(f"pulse too short ({width_ms:.1f} ms) - noise")
            return
        if width_ms > self.cfg.pulse_max_ms:
            self._reject(f"pulse too long ({width_ms:.1f} ms)")
            return
        if self._last_valid_end_ns is not None:
            gap_ms = (start_ns - self._last_valid_end_ns) / 1e6
            if gap_ms < self.cfg.min_gap_ms:
                self._reject(f"gap too short ({gap_ms:.1f} ms) - bounce")
                return
        self._last_valid_end_ns = t_ns

        if self.cfg.pulses_per_coin == 1:
            self._accept(1)
        else:
            self._burst += 1
            self._burst_last_mono = now_mono

    # ------------------------------------------------------------------- poll
    def poll(self, now_mono: float) -> None:
        if (self._pulse_start_mono is not None and not self._stuck_reported
                and now_mono - self._pulse_start_mono >= self.cfg.stuck_active_s):
            self._stuck_reported = True
            self._reject("coin line stuck active - check wiring")
        if self._burst and now_mono - self._burst_last_mono >= self.cfg.burst_settle_ms / 1000.0:
            ppc = self.cfg.pulses_per_coin
            coins, leftover = divmod(self._burst, ppc)
            self._burst = 0
            for _ in range(coins):
                self._accept(ppc)
            if leftover:
                self._reject(f"incomplete pulse train ({leftover} extra pulse(s))")

    # --------------------------------------------------------------- helpers
    def _accept(self, pulses: int) -> None:
        self.accepted += 1
        try:
            self.on_coin(self.cfg.value, pulses)
        except Exception:  # never let a consumer kill the decoder
            log.exception("on_coin callback failed")

    def _reject(self, reason: str) -> None:
        self.rejected += 1
        log.info("coin pulse rejected: %s", reason)
        try:
            self.on_reject(reason)
        except Exception:
            log.exception("on_reject callback failed")


class CoinWorker:
    """Owns a CoinDecoder on its own thread. ``push_edge`` is safe to call
    from any thread (it only enqueues)."""

    POLL_S = 0.02

    def __init__(self, cfg: CoinCfg, on_coin: CoinCallback, on_reject: RejectCallback):
        self.decoder = CoinDecoder(cfg, on_coin, on_reject)
        self._q: "queue.Queue[tuple[int, int, float]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="coin-decoder", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def push_edge(self, level: int, t_ns: int) -> None:
        self._q.put((level, t_ns, time.monotonic()))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                level, t_ns, mono = self._q.get(timeout=self.POLL_S)
                self.decoder.edge(level, t_ns, mono)
            except queue.Empty:
                pass
            except Exception:
                log.exception("coin decoder error")
            self.decoder.poll(time.monotonic())
