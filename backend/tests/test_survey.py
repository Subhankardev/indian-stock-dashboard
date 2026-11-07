import time

import pytest
import requests

from app import survey, upstox_data
from app.wave_oi import make_synthetic
from app.wave_oi.tuning import DEFAULTS


def test_wilson_interval():
    lo, hi = survey.wilson(50, 100)
    assert lo == pytest.approx(0.404, abs=0.002) and hi == pytest.approx(0.596, abs=0.002)
    lo, hi = survey.wilson(0, 10)
    assert lo == 0 and 0.25 < hi < 0.35
    assert survey.wilson(0, 0) == (None, None)


def test_mean_ci():
    lo, hi = survey.mean_ci([1.0, -1.0, 1.0, -1.0])
    assert lo < 0 < hi and survey.mean_ci([1.0]) == (None, None)


def test_parse_index_csv():
    text = "Company Name,Industry,Symbol,Series,ISIN Code\nReliance,Oil,RELIANCE,EQ,X\nM&M,Auto,M&M,EQ,Y\n"
    assert survey.parse_index_csv(text) == ["RELIANCE", "M&M"]
    with pytest.raises(ValueError):
        survey.parse_index_csv("a,b\n1,2\n")


def test_nifty50_download_and_fallback():
    class Ok:
        def get(self, *a, **k):
            class R:
                status_code = 200
                text = "Symbol\n" + "\n".join(f"S{i}" for i in range(50))
            return R()

    class Down:
        def get(self, *a, **k):
            raise requests.ConnectionError("blocked")

    syms, note = survey.nifty50(Ok())
    assert len(syms) == 50 and "current" in note
    syms, note = survey.nifty50(Down())
    assert syms == survey.NIFTY50_FALLBACK and len(syms) == 50 and "built-in" in note


def test_run_symbol_counts():
    r = survey.run_symbol(make_synthetic(600, seed=7), dict(DEFAULTS))
    assert r["trades"] <= r["buy_signals"] + r["sell_signals"]
    assert r["wins"] <= r["trades"] and len(r["rs"]) == r["trades"]
    assert r["win_rate"] == pytest.approx(r["wins"] / r["trades"])


def test_survey_pooling_and_variants():
    dfs = {f"S{i}": make_synthetic(500, seed=i) for i in range(1, 4)}
    res = survey.survey(dfs, dict(DEFAULTS))
    t = res["total"]
    assert t["stocks"] == 3
    assert t["trades"] == sum(r["trades"] for r in res["per_symbol"].values())
    assert t["signals"] == sum(r["buy_signals"] + r["sell_signals"] for r in res["per_symbol"].values())
    assert list(res["variants"]) == list(survey.VARIANTS)
    # dropping filters can only allow more signals, never fewer
    v = res["variants"]
    assert v["Wave + GTI (no OI filter)"]["signals"] >= v["Wave + GTI + OI (active rules)"]["signals"]


def test_verdicts():
    base = {"trades": 200, "avg_r": 0.3}
    assert "Positive edge" in survey.verdict({**base, "avg_r_ci": (0.1, 0.5)})
    assert "Negative edge" in survey.verdict({**base, "avg_r": -0.3, "avg_r_ci": (-0.5, -0.1)})
    assert "No clear edge" in survey.verdict({**base, "avg_r": 0.05, "avg_r_ci": (-0.1, 0.2)})
    assert "too few" in survey.verdict({"trades": 12, "avg_r": 1.0, "avg_r_ci": (0.2, 1.8)})


def test_cli_writes_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(survey, "BACKEND", tmp_path)
    assert survey.main(["--source", "synthetic"]) == 0
    out = capsys.readouterr().out
    assert "Signals generated" in out and "Win rate" in out
    report = next((tmp_path / "reports").glob("survey-*.md")).read_text()
    assert "## By stock" in report and "## Which part helps?" in report


# -------------------------------------------------------- Upstox pacing -- #
class FakeSession(requests.Session):
    """A real Session subclass (so pacing applies) that never touches the network."""

    def __init__(self, statuses):
        super().__init__()
        self.statuses, self.times = list(statuses), []

    def get(self, url, **kw):
        self.times.append(time.monotonic())
        status = self.statuses.pop(0) if self.statuses else 200

        class R:
            status_code = status
            ok = status == 200
            text = ""

            def json(self):
                return {"status": "success", "data": {}}
        return R()


def test_real_sessions_are_paced(monkeypatch):
    monkeypatch.setenv("UPSTOX_MIN_INTERVAL", "0.05")
    s = FakeSession([])
    c = upstox_data.UpstoxClient(access_token="t", session=s)
    for _ in range(3):
        c._get("/x")
    gaps = [b - a for a, b in zip(s.times, s.times[1:])]
    assert min(gaps) >= 0.045


def test_rate_limit_backs_off_and_retries(monkeypatch):
    monkeypatch.setenv("UPSTOX_MIN_INTERVAL", "0")
    slept = []
    monkeypatch.setattr(upstox_data.time, "sleep", lambda s: slept.append(s))
    s = FakeSession([429, 429, 200])
    assert upstox_data.UpstoxClient(access_token="t", session=s)._get("/x")["status"] == "success"
    assert slept == [1.0, 2.0] and len(s.times) == 3
    s = FakeSession([429, 429, 429, 429])
    with pytest.raises(upstox_data.UpstoxError, match="429"):
        upstox_data.UpstoxClient(access_token="t", session=s)._get("/x")
