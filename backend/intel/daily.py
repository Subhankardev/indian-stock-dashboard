"""The daily job, in order. Run after ~18:30 IST on trading days:

    python -m intel.daily

1. prices and F&O:
   * NSE archives (bhavcopy with delivery, indices, F&O, participant OI) + bulk/block deals + FII/DII
     when ``data.source`` is "nse" or "both";
   * Upstox API (last 10 days of daily candles, indices, futures OI, today's NIFTY/BANKNIFTY
     option-chain summary) when ``data.source`` is "upstox" or "both" and UPSTOX_ACCESS_TOKEN is set
2. update paper trades against the new bars                    (intel.paper --update)
3. generate alerts and send them to Telegram if configured      (intel.alerts --run)

Re-running is safe: every step is idempotent. A step that fails (e.g. NSE blocking your network)
is reported and the rest still run.
"""
from __future__ import annotations

import os
import sys


def main(argv=None) -> int:
    from . import alerts, db, ingest, paper, upstox_ingest
    from .config import load
    source = load()["data"]["source"]
    rc = 0
    if source in ("nse", "both"):
        try:
            rc = ingest.main(["--today"]) or 0
        except Exception as exc:  # NSE unreachable etc.
            print(f"NSE download failed: {exc}")
            rc = 1
    if source in ("upstox", "both"):
        if os.getenv("UPSTOX_ACCESS_TOKEN"):
            args = ["--days", "10", "--options"] + (["--fo-only"] if load()["data"].get("upstox_fo_only") else [])
            try:
                rc = upstox_ingest.main(args) or rc
            except Exception as exc:
                print(f"Upstox download failed: {exc}")
                rc = 1
        else:
            print("Skipping Upstox: set UPSTOX_ACCESS_TOKEN in backend/.env (login: /api/upstox/login)")
    print("Paper:", paper.update_all(db.engine()))
    alerts.main(["--run"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
