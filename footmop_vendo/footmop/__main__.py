"""Entry point:  python3 -m footmop [--config config.toml] [--mock] [--windowed]"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys

from . import APP_NAME, __version__
from .config import ConfigError, load_config


def setup_logging(log_dir: str, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh = logging.StreamHandler(sys.stderr)          # -> journald under systemd
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "footmop.log"), maxBytes=2_000_000, backupCount=5)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        root.warning("file logging disabled (%s): %s", log_dir, exc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="footmop", description=f"{APP_NAME} vending kiosk")
    ap.add_argument("--config", default=os.environ.get("FOOTMOP_CONFIG"),
                    help="path to config.toml")
    ap.add_argument("--mock", action="store_true",
                    help="simulate hardware with the keyboard (for testing on a PC)")
    ap.add_argument("--windowed", action="store_true", help="run in a window, not fullscreen")
    ap.add_argument("--size", default=None, help="window size WxH, e.g. 800x480")
    ap.add_argument("--db", default=None, help="override storage.db_path")
    ap.add_argument("--seed-stock", type=int, default=None,
                    help="set stock to this value at start (testing)")
    ap.add_argument("--screenshots", default=None, help=argparse.SUPPRESS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.mock and not args.db and not args.config:
        args.db = os.path.join(os.getcwd(), "data", "footmop-mock.db")
    if args.db:
        cfg.storage.db_path = args.db
    if args.mock and not args.config:
        cfg.storage.log_dir = os.path.join(os.getcwd(), "data", "logs")
        cfg.storage.export_dir = os.path.join(os.getcwd(), "data", "exports")
    setup_logging(cfg.storage.log_dir, args.verbose)
    logging.getLogger("footmop").info("%s v%s starting (mock=%s)", APP_NAME, __version__, args.mock)

    if args.seed_stock is not None:
        from .core.store import Store
        s = Store(cfg.storage.db_path, cfg.machine.price_per_item)
        s.set_stock(args.seed_stock, "seed")
        s.close()

    size = None
    if args.size:
        w, h = args.size.lower().split("x")
        size = (int(w), int(h))
    fullscreen = cfg.display.fullscreen and not args.windowed

    if not args.mock:
        # lgpio creates a notification pipe in the process working directory.
        # /opt/footmop is normally root-owned, so use the app's writable data
        # directory instead of failing during lgpio import.
        runtime_dir = os.path.dirname(cfg.storage.db_path) or "/tmp"
        os.makedirs(runtime_dir, exist_ok=True)
        os.chdir(runtime_dir)

    from .ui.app import run
    return run(cfg, args.mock, fullscreen, size, args.screenshots)


if __name__ == "__main__":
    sys.exit(main())
