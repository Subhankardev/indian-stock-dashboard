"""Order-flow features that need tick-by-tick or order-book data (Phase 4, feature-flagged).

None of these can be computed honestly from daily bhavcopies or 1-minute candles, so they
are **disabled** until a licensed feed is configured. The API lists each with the data it
needs, so the UI can explain exactly why it is off rather than showing a proxy as the real
thing.

Enable in ``backend/intel_config.json``::

    {"orderflow": {"enabled": true, "provider": "nse_tbt"}}

…and implement ``load_ticks(symbol, day)`` for that provider. Until then ``status()`` reports
every feature as unavailable.
"""
from __future__ import annotations

from .config import load

FEATURES = {
    "cumulative_delta": {
        "label": "Cumulative volume delta",
        "needs": "every trade with aggressor side (buyer- or seller-initiated)",
        "why": "daily and minute bars don't say which side initiated each trade"},
    "large_trades": {
        "label": "Large-trade / block-print detection",
        "needs": "individual trades with size and time",
        "why": "bars aggregate trades, so one 50,000-share print looks the same as 500 small ones"},
    "bid_ask_imbalance": {
        "label": "Order-book imbalance",
        "needs": "level-2/level-5 order-book snapshots (or full depth)",
        "why": "resting bids and offers are not in any free NSE file"},
    "tick_absorption": {
        "label": "Absorption (aggressive volume without price progress)",
        "needs": "trades with aggressor side plus best bid/offer",
        "why": "the daily 'absorption_up/down' clue is a bar-level proxy only"},
    "iceberg_detection": {
        "label": "Iceberg / hidden-order detection",
        "needs": "full order-book updates (add/modify/cancel)",
        "why": "requires order-level messages"},
}

PROVIDERS = [
    {"name": "NSE tick-by-tick (TBT) historical / live", "type": "licensed exchange feed", "cost": "paid licence"},
    {"name": "Authorised data vendors (e.g. TrueData, Global Datafeeds)", "type": "vendor feed", "cost": "paid subscription"},
    {"name": "Broker WebSocket full-depth feeds", "type": "live only, no history", "cost": "account-dependent"},
]


def status() -> dict:
    cfg = load().get("orderflow", {})
    enabled = bool(cfg.get("enabled")) and bool(cfg.get("provider"))
    feats = []
    for k, f in FEATURES.items():
        feats.append({"key": k, **f, "available": False,
                      "reason": ("provider configured but no loader is implemented yet" if enabled
                                 else "disabled — no tick or order-book data source configured")})
    return {"enabled": enabled, "provider": cfg.get("provider"), "features": feats, "providers": PROVIDERS,
            "available_instead": ["Daily VWAP / anchored VWAP (exact, from NSE average price)",
                                  "Intraday VWAP bands and volume profile from Upstox 1-minute candles (bar approximation)",
                                  "Daily-bar absorption proxy (class D)"]}
