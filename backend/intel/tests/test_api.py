from fastapi.testclient import TestClient

from app.main import app
from intel import db, demo


def client():
    return TestClient(app)


def test_empty_database_explains_what_to_run():
    r = client().get("/api/intel/screener").json()
    assert r["rows"] == [] and "intel.ingest" in r["message"]
    s = client().get("/api/intel/status").json()
    assert s["demo"] is False and s["datasets"] == [] and s["registry"]


def test_demo_screener_stock_and_research():
    demo.seed(db.engine(), demo.make(n_symbols=20, days=320))
    c = client()
    s = c.get("/api/intel/screener", params={"liquid_only": False, "limit": 5}).json()
    assert s["demo"] is True and s["shown"] == 5 and s["as_of"] == "2026-11-20"
    ranks = [r["rs_rank"] for r in s["rows"]]
    assert ranks == sorted(ranks, reverse=True) and s["sources"][0]["dataset"] == "demo"
    sym = s["rows"][0]["symbol"]
    d = c.get(f"/api/intel/stock/{sym.lower()}", params={"days": 60}).json()
    assert len(d["series"]) == 60 and d["source"]["dataset"] == "demo" and d["latest"]["rs_rank"] is not None
    assert c.get("/api/intel/stock/NOPE").status_code == 404
    assert c.get("/api/intel/screener", params={"sort": "bogus"}).status_code == 422
    r = c.post("/api/intel/research/run", params={"signal": "rs_leaders"}).json()
    assert [x["signal_name"] for x in r["results"]] == ["rs_leaders"] and "passed" in r["results"][0]["stats"]
    assert c.post("/api/intel/research/run", params={"signal": "nope"}).status_code == 422
    assert "rs_leaders" in c.get("/api/intel/research").json()["signals"]
