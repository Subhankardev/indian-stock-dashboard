import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import tune as tune_cli
from app.main import app
from app.wave_oi import make_synthetic, tuning

SMALL_GRID = {
    "zz_atr_mult": [2.0, 2.5],
    "gti_mult": [2.5],
    "min_rr": [1.0, 1.5],
    "stop_mode": ["pivot"],
    "use_oi": [True],
    "trade_corrections": [False],
    "exit_on_gti_flip": [True],
}


@pytest.fixture(scope="module")
def dfs():
    return {f"S{i}": make_synthetic(600, seed=i) for i in range(1, 4)}


def test_r_stats():
    st = tuning.r_stats([2.0, -1.0, 1.0, -1.0])
    assert st["trades"] == 4 and st["win_rate"] == 0.5 and st["total_r"] == 1.0 and st["avg_r"] == 0.25
    assert st["profit_factor"] == pytest.approx(1.5)
    assert st["max_dd_r"] == pytest.approx(-1.0)  # peak 2, low 1
    assert tuning.r_stats([])["trades"] == 0 and tuning.r_stats([1.0])["sqn"] is None
    assert tuning.r_stats([-1.0, -2.0])["max_dd_r"] == -3.0


def test_net_r_includes_fees():
    t = {"qty": 10, "entry": 100.0, "stop": 95.0, "pnl": 95.0}
    assert tuning.net_r(t) == pytest.approx(95 / 50)


def test_score_needs_enough_trades():
    assert tuning.score({"trades": 3, "sqn": 2.0}, min_trades=5) == float("-inf")
    assert tuning.score({"trades": 9, "sqn": 2.0}, min_trades=5) == 2.0


def test_rules_roundtrip_and_bad_file(tmp_path):
    assert tuning.load_rules(tmp_path / "none.json")["source"] == "defaults"
    p = tmp_path / "s.json"
    tuning.save_rules({**tuning.DEFAULTS, "min_rr": 2.5, "use_oi": False}, {"x": 1}, p)
    r = tuning.load_rules(p)
    assert r["source"] == "tuned" and r["params"]["min_rr"] == 2.5 and r["params"]["use_oi"] is False
    p.write_text("{broken")
    assert tuning.load_rules(p)["source"].startswith("defaults")
    p.write_text(json.dumps({"params": {"min_rr": 3.0, "evil_key": 1}}))
    assert "evil_key" not in tuning.load_rules(p)["params"]


def test_split_params():
    s, b = tuning.split_params({**tuning.DEFAULTS, "zz_atr_mult": 3.0, "exit_on_gti_flip": False})
    assert s.zz_atr_mult == 3.0 and b.exit_on_gti_flip is False


def test_tune_walk_forward(dfs):
    res = tuning.tune(dfs, grid=SMALL_GRID, min_trades=3)
    assert res["combos_tested"] == 4, "defaults are already in this grid"
    no_defaults = {**SMALL_GRID, "min_rr": [1.0, 2.0]}
    assert tuning.tune(dfs, grid=no_defaults, min_trades=3)["combos_tested"] == 4 + 1, \
        "the default settings are always compared"
    assert set(res["best"]) == set(SMALL_GRID)
    # every out-of-sample trade is entered on/after that symbol's split date
    splits = {s: pd.Timestamp(d) for s, d in res["splits"].items()}
    scfg, bcfg = tuning.split_params(res["best"])
    from app.wave_oi import analyze, run_backtest
    for sym, df in dfs.items():
        trades = run_backtest(analyze(df, scfg)[0], bcfg)["trades"]
        n_oos = sum(pd.Timestamp(t["entry_date"]) >= splits[sym] for t in trades)
        row = next(r for r in res["best_by_symbol"] if r["symbol"] == sym)
        assert row["trades"] == n_oos
    report = tuning.render_report(res)
    assert "out-of-sample" in report and "Default vs tuned rules" in report


def test_tune_rejects_when_too_few_trades(dfs):
    with pytest.raises(ValueError, match="in-sample trades"):
        tuning.tune(dfs, grid=SMALL_GRID, min_trades=10_000)


def test_cli_apply_and_reset(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tuning, "PARAM_GRID", SMALL_GRID)
    monkeypatch.setattr(tune_cli, "BACKEND", tmp_path)
    assert tune_cli.main(["--source", "synthetic", "--symbols", "A,B,C", "--days", "800",
                          "--min-trades", "3", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "Out-of-sample" in out and "Saved to" in out
    assert tuning.RULES_FILE.exists() and list((tmp_path / "reports").glob("tuning-*.md"))
    assert tune_cli.main(["--reset"]) == 0 and not tuning.RULES_FILE.exists()


def test_api_uses_tuned_rules_and_reports_them():
    c = TestClient(app)
    body = c.get("/api/wave-oi/DEMO", params={"source": "synthetic", "days": 900}).json()
    assert body["rules"]["source"] == "defaults" and body["rules"]["params"]["min_rr"] == 1.5
    tuning.save_rules({**tuning.DEFAULTS, "min_rr": 2.5, "zz_atr_mult": 3.0}, {})
    body = c.get("/api/wave-oi/DEMO", params={"source": "synthetic", "days": 900}).json()
    assert body["rules"]["source"] == "tuned" and body["rules"]["params"]["zz_atr_mult"] == 3.0
    body = c.get("/api/wave-oi/DEMO", params={"source": "synthetic", "days": 900, "min_rr": 1.0}).json()
    assert body["rules"]["params"]["min_rr"] == 1.0, "an explicit query parameter still wins"


def test_alert_scanner_uses_tuned_rules():
    from app.alerts import routes as alert_routes
    tuning.save_rules({**tuning.DEFAULTS, "zz_atr_mult": 3.5}, {})
    alert_routes.reset_scanner(None)
    assert alert_routes.get_scanner().config.zz_atr_mult == 3.5
