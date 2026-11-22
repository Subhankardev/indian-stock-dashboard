"""The daily job, in order. Run after ~18:30 IST on trading days:

    python -m intel.daily

1. download the last few days of NSE files, deals and FII/DII  (intel.ingest --today)
2. update paper trades against the new bars                    (intel.paper --update)
3. generate alerts and send them to Telegram if configured      (intel.alerts --run)

Re-running is safe: every step is idempotent.
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    from . import alerts, ingest, paper
    rc = ingest.main(["--today"])
    print("Paper:", paper.update_all(__import__("intel.db", fromlist=["engine"]).engine()))
    alerts.main(["--run"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
