"""Sales report CSV export to a USB flash drive (or a local folder).

The kiosk has no keyboard or browser, so "download" means: write the CSV to
the first mounted USB drive found, flush it to disk, and tell the owner it's
safe to unplug. With no drive present (or on a PC in simulator mode) the file
goes to a local fallback folder instead.
"""
from __future__ import annotations

import csv
import datetime as _dt
import glob
import logging
import os
from typing import List, Optional, Sequence, Tuple

from .store import CANCELLED, Store

log = logging.getLogger("footmop.export")

COLUMNS = ["order_id", "started", "ended", "status", "quantity", "unit_price",
           "amount_due", "amount_paid", "dispensed", "owed", "note"]


def _safe(v):
    """Stop spreadsheet formula injection in free-text cells."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def find_usb_mounts(patterns: Sequence[str]) -> List[str]:
    """Mounted, writable directories matching the glob patterns."""
    found = []
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            if os.path.isdir(path) and os.path.ismount(path) and os.access(path, os.W_OK):
                if path not in found:
                    found.append(path)
    return found


def write_sales_csv(store: Store, out_dir: str, now: Optional[_dt.datetime] = None) -> Tuple[str, int]:
    """Write every non-cancelled order to a new CSV in out_dir.
    Returns (file path, number of rows)."""
    now = now or _dt.datetime.now()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"footmop_sales_{now:%Y%m%d_%H%M%S}.csv")
    rows = store.db.execute(
        "SELECT * FROM sessions WHERE status != ? ORDER BY id", (CANCELLED,)).fetchall()
    tmp = path + ".part"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:   # BOM so Excel opens it cleanly
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r["id"], (r["started_at"] or "").replace("T", " "),
                        (r["ended_at"] or "").replace("T", " "), r["status"], r["qty"],
                        r["unit_price"], r["amount_due"], r["credit"], r["dispensed"],
                        max(0, r["qty"] - r["dispensed"]) if r["status"] in ("PARTIAL_FAULT", "INTERRUPTED") else 0,
                        _safe(r["note"] or "")])
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        os.sync()          # make sure it's really on the stick before it's pulled out
    except AttributeError:  # pragma: no cover - Windows
        pass
    log.info("exported %d orders to %s", len(rows), path)
    return path, len(rows)


def export_sales(store: Store, usb_patterns: Sequence[str], fallback_dir: str) -> Tuple[str, int, bool]:
    """Export to the first USB drive, else the fallback folder.
    Returns (path, rows, on_usb). Raises OSError if nothing could be written."""
    for mount in find_usb_mounts(usb_patterns):
        try:
            path, n = write_sales_csv(store, mount)
            return path, n, True
        except OSError:
            log.exception("could not write to USB drive %s", mount)
    path, n = write_sales_csv(store, fallback_dir)
    return path, n, False
