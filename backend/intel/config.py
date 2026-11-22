"""Settings for the Institutional Intelligence system.

Screening and scoring parameters live in ``backend/intel_config.json`` so they can be
changed without touching code. Anything not in that file falls back to DEFAULTS.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("INTEL_DATA_DIR") or BACKEND / "data")
CONFIG_FILE = Path(os.getenv("INTEL_CONFIG") or BACKEND / "intel_config.json")

DEFAULTS: dict = {
    "universe": {
        "name": "all",                 # all | nifty50 | nifty100 | nifty200 | nifty500 | fno | custom
        "custom": [],
        "series": ["EQ", "BE", "BZ"],  # equity series kept from the bhavcopy
    },
    "liquidity": {
        "min_avg_traded_value_cr": 10.0,   # ₹ crore, 20-session average
        "min_avg_volume": 50_000,          # shares, 20-session average
        "min_history_sessions": 200,
        "min_price": 20.0,
        "max_daily_volatility_pct": 8.0,   # 20-day stdev of daily returns, %
        "lookback_sessions": 20,
        "min_market_cap_cr": 1000.0,       # applied once market cap is available (Phase 2 shareholding)
    },
    "relative_strength": {
        "benchmark": "Nifty 50",
        "broad_benchmarks": ["Nifty 50", "Nifty 100", "Nifty 500"],
        "windows": {"5d": 5, "10d": 10, "20d": 20, "50d": 50, "3m": 63, "6m": 126, "12m": 252},
        "rank_window": "6m",
    },
    "research": {
        "horizons": [5, 10, 20, 60],
        "benchmark": "Nifty 500",
        "cost_bps_round_trip": 30,      # brokerage+STT+fees+slippage, liquid stocks
        "pass_min_events": 200,
        "pass_horizon": 20,
        "pass_min_positive_subperiods": 2,
        "subperiods": 3,
    },
    "scoring": {
        "weights": {
            "accumulation": 20, "relative_strength": 15, "volume_delivery": 15, "momentum": 15,
            "fundamentals": 10, "derivatives": 10, "sector": 5, "valuation": 5, "risk": 5,
        },
        "min_confidence": 0.6,
        # a component enters a score only after its signal passed its event study
        "require_pass": True,
    },
    "nse": {"min_interval_s": 0.5, "timeout_s": 30, "retries": 3,
            # F&O option rows are ~50-100k a day; only per-underlying summaries are kept unless True
            "store_option_rows": False},
    "orderflow": {"enabled": False, "provider": None},
    "fundamentals": {"min_peers": 5, "max_age_days": 270, "quality_filter_min": 60},
    "derivatives": {"risk_free_rate": 0.065, "oi_change_window": 5, "min_oi_change_pct": 5.0,
                    "expiry_blackout_sessions": 3},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: Path | None = None) -> dict:
    path = Path(path) if path else CONFIG_FILE
    if path.is_file():
        try:
            return _merge(DEFAULTS, json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    return copy.deepcopy(DEFAULTS)


def save(cfg: dict, path: Path | None = None) -> Path:
    path = Path(path) if path else CONFIG_FILE
    path.write_text(json.dumps(cfg, indent=2))
    return path
