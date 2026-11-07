from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.alerts import routes as alert_routes
from app.alerts.engine import IST, AlertScanner, AlertStore, market_phase
from app.main import app
from app.wave_oi import analyze, make_synthetic

WED = datetime(2026, 11, 4, tzinfo=IST)  # a Wednesday


def at(day: datetime, hh: int, mm: int = 0) -> datetime:
    return day.replace(hour=hh, minute=mm)


@pytest.fixture(scope="module")
def frames():
    """(history whose last bar carries a signal, same history one bar shorter = no signal)."""
    full = make_synthetic(600, seed=7)
    an, _ = analyze(full)
    k = int((an["signal"] != 0).to_numpy().argmax())
    assert an["signal"].iloc[k] != 0
    with_sig, without = full.iloc[: k + 1], full.iloc[:k]
    assert analyze(without)[0]["signal"].iloc[-1] == 0
    return with_sig, without, int(an["signal"].iloc[k])


def dated(df: pd.DataFrame, last_day: datetime) -> pd.DataFrame:
    """Re-date the bars so the last one falls on ``last_day``."""
    out = df.copy()
    out.index = pd.bdate_range(end=last_day.date(), periods=len(df), name="date")
    return out


class Rig:
    def __init__(self, tmp_path):
        self.store = AlertStore(tmp_path / "a.json")
        self.store.set_paused(False)
        self.store.set_watchlist(["ABC"])
        self.df = None
        self.clock = None
        self.scanner = AlertScanner(self.store, lambda s: (self.df, None), now=lambda: self.clock)

    def scan(self, df, clock):
        self.df, self.clock = df, clock
        return [(a["kind"], a["side"]) for a in self.scanner.scan_all()]


# ---------------------------------------------------------------- phases --- #
@pytest.mark.parametrize("when,phase", [
    (at(WED, 9, 14), "closed"), (at(WED, 9, 15), "open"), (at(WED, 15, 29), "open"),
    (at(WED, 15, 30), "closed"), (at(WED.replace(day=7), 11), "closed"),  # Saturday
])
def test_market_phase(when, phase):
    assert market_phase(when) == phase


# --------------------------------------------------------- state machine --- #
def test_forming_fading_and_confirmation_over_a_day(tmp_path, frames):
    with_sig, without, sig = frames
    side = "BUY" if sig == 1 else "SELL"
    r = Rig(tmp_path)
    assert r.scan(dated(with_sig, WED), at(WED, 11)) == [("forming", side)]
    assert r.scan(dated(with_sig, WED), at(WED, 11, 1)) == [], "no repeat while it stays formed"
    assert r.scan(dated(without, WED), at(WED, 12)) == [("faded", side)]
    assert r.scan(dated(with_sig, WED), at(WED, 13)) == [("forming", side)], "can form again"
    assert r.scan(dated(with_sig, WED), at(WED, 15, 45)) == [("confirmed", side)]
    assert r.scan(dated(with_sig, WED), at(WED, 18)) == [], "confirmed once"
    thu = WED.replace(day=5)
    assert r.scan(dated(with_sig, WED), at(thu, 10)) == [], "yesterday's bar is not 'forming' today"


def test_signal_that_does_not_hold_into_close_fades(tmp_path, frames):
    with_sig, without, sig = frames
    r = Rig(tmp_path)
    r.scan(dated(with_sig, WED), at(WED, 15))
    assert r.scan(dated(without, WED), at(WED, 15, 40)) == [("faded", "BUY" if sig == 1 else "SELL")]
    assert r.store.data["alerts"][-1]["reason"] == "Did not hold into the close"


def test_first_scan_after_close_confirms_directly(tmp_path, frames):
    with_sig, _, sig = frames
    r = Rig(tmp_path)
    kinds = r.scan(dated(with_sig, WED), at(WED, 20))
    assert kinds == [("confirmed", "BUY" if sig == 1 else "SELL")]
    a = r.store.data["alerts"][-1]
    assert a["bar_date"] == "2026-11-04" and a["stop"] and a["target"] and a["reason"]


def test_holiday_bar_from_previous_day_is_not_forming(tmp_path, frames):
    with_sig, _, _ = frames
    r = Rig(tmp_path)
    tue = WED.replace(day=3)
    # market "open" by the clock on Wednesday, but the newest bar is Tuesday's (holiday)
    assert [k for k, _ in r.scan(dated(with_sig, tue), at(WED, 11))] == ["confirmed"]


def test_one_failing_symbol_does_not_stop_the_scan(tmp_path, frames):
    with_sig, _, _ = frames
    store = AlertStore(tmp_path / "a.json")
    store.set_paused(False)
    store.set_watchlist(["BAD", "GOOD"])

    def load(sym):
        if sym == "BAD":
            raise RuntimeError("no data")
        return dated(with_sig, WED), None

    sc = AlertScanner(store, load, now=lambda: at(WED, 20))
    new = sc.scan_all()
    assert [a["symbol"] for a in new] == ["GOOD"]
    assert "no data" in sc.status["errors"]["BAD"] and sc.status["phase"] == "closed"


# ---------------------------------------------------------------- store ---- #
def test_store_persists_and_validates(tmp_path):
    s = AlertStore(tmp_path / "a.json")
    assert s.set_watchlist(["reliance", "NIFTY", "reliance", " "]) == ["RELIANCE", "NIFTY"]
    s.emit({"symbol": "X", "kind": "confirmed", "side": "BUY", "bar_date": "2026-11-04"})
    s2 = AlertStore(tmp_path / "a.json")
    assert s2.watchlist == ["RELIANCE", "NIFTY"] and s2.since(0)[0]["id"] == 1
    assert s2.has("X", "2026-11-04", "BUY", "confirmed")
    with pytest.raises(ValueError):
        s.set_watchlist(["BAD SYMBOL!"])


def test_corrupt_store_file_starts_fresh(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{not json")
    assert AlertStore(p).watchlist == []


# ------------------------------------------------------------------ API ---- #
def test_alerts_api(tmp_path, frames):
    with_sig, _, _ = frames
    store = AlertStore(tmp_path / "api.json")
    store.set_paused(False)
    alert_routes.reset_scanner(AlertScanner(store, lambda s: (dated(with_sig, WED), None),
                                            now=lambda: at(WED, 20)))
    c = TestClient(app)
    assert c.post("/api/alerts/watchlist/reliance").json()["watchlist"] == ["RELIANCE"]
    assert c.post("/api/alerts/watchlist/NIFTY").json()["watchlist"] == ["RELIANCE", "NIFTY"]
    assert c.delete("/api/alerts/watchlist/reliance").json()["watchlist"] == ["NIFTY"]
    assert c.put("/api/alerts/watchlist", json={"symbols": ["not ok!"]}).status_code == 422

    t = c.post("/api/alerts/test").json()
    assert t["kind"] == "test" and t["id"] == 1
    new = c.post("/api/alerts/scan").json()["new"]
    assert [a["kind"] for a in new] == ["confirmed"] and new[0]["symbol"] == "NIFTY"

    body = c.get("/api/alerts", params={"since": 1}).json()
    assert [a["id"] for a in body["alerts"]] == [2] and body["last_id"] == 2
    assert body["watchlist"] == ["NIFTY"] and body["scanner"]["phase"] == "closed"
    assert len(c.get("/api/alerts").json()["alerts"]) == 2


def test_background_scanner_runs_with_the_app(tmp_path, monkeypatch, frames):
    import time
    with_sig, _, _ = frames
    monkeypatch.setenv("ALERTS_ENABLED", "1")
    store = AlertStore(tmp_path / "bg.json")
    store.set_paused(False)
    store.set_watchlist(["ABC"])
    alert_routes.reset_scanner(AlertScanner(store, lambda s: (dated(with_sig, WED), None),
                                            now=lambda: at(WED, 20)))
    with TestClient(app) as c:
        for _ in range(50):
            b = c.get("/api/alerts").json()
            if b["alerts"] and b["scanner"]["next_run"]:  # scan done and next one scheduled
                break
            time.sleep(0.05)
        body = c.get("/api/alerts").json()
    assert [a["kind"] for a in body["alerts"]] == ["confirmed"]
    assert body["scanner"]["next_run"]


def test_dismiss_and_clear_alerts(tmp_path):
    store = AlertStore(tmp_path / "c.json")
    alert_routes.reset_scanner(AlertScanner(store, lambda s: (None, None)))
    c = TestClient(app)
    for _ in range(3):
        c.post("/api/alerts/test")
    store.emit({"symbol": "X", "kind": "confirmed", "side": "BUY", "bar_date": "2026-11-04"})
    assert c.delete("/api/alerts/2").json() == {"removed": 1}
    assert c.delete("/api/alerts/2").status_code == 404
    assert c.delete("/api/alerts", params={"kind": "test"}).json() == {"removed": 2}
    assert [a["kind"] for a in c.get("/api/alerts").json()["alerts"]] == ["confirmed"]
    assert c.delete("/api/alerts", params={"kind": "bogus"}).status_code == 422
    assert c.delete("/api/alerts", params={"kind": "all"}).json() == {"removed": 1}
    assert AlertStore(tmp_path / "c.json").since(0) == [], "removal is persisted"
    assert c.post("/api/alerts/test").json()["id"] == 5, "ids keep counting up after a clear"



def test_alerts_are_paused_by_default_and_can_resume(tmp_path, frames):
    with_sig, _, _ = frames
    store = AlertStore(tmp_path / "p.json")
    assert store.paused is True, "paused unless the user opts back in"
    store.set_watchlist(["ABC"])
    calls = []

    def load(sym):
        calls.append(sym)
        return dated(with_sig, WED), None

    sc = AlertScanner(store, load, now=lambda: at(WED, 20))
    assert sc.scan_all() == [] and calls == [] and sc.status["paused"] is True, "no data fetched while paused"
    alert_routes.reset_scanner(sc)
    c = TestClient(app)
    assert c.get("/api/alerts").json()["paused"] is True
    assert c.post("/api/alerts/resume").json() == {"paused": False}
    assert AlertStore(tmp_path / "p.json").paused is False, "resume is saved"
    assert [a["kind"] for a in sc.scan_all()] == ["confirmed"]
    assert c.post("/api/alerts/pause").json() == {"paused": True} and sc.scan_all() == []
