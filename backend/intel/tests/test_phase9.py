import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from intel import assistant, db, demo
from intel.panel import build_panel
from intel.scoring import enrich


def usage():
    return NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=3, cache_creation_input_tokens=0)


class FakeClient:
    """Scripted stand-in for anthropic.Anthropic(): records requests, replays responses."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        return self.script.pop(0)


def tool_use(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


@pytest.fixture
def tools():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=20, days=320))
    p = enrich(eng, build_panel(eng))
    return assistant.Tools(eng, lambda: p)


def test_every_tool_runs_on_demo_data(tools):
    calls = {"data_status": {}, "market_overview": {}, "derivatives_overview": {}, "paper_trading": {},
             "screen_stocks": {"sort_by": "rs_rank", "ascending": False, "industry": None, "limit": 5},
             "stock_report": {"symbol": "demo03"}, "rankings": {"preview": True, "limit": 5},
             "institutional_activity": {"symbol": None, "days": 365}, "research_results": {"signal": None},
             "backtests": {"run_id": None}}
    assert set(calls) == {t["name"] for t in assistant.TOOLS}
    for name, args in calls.items():
        out = tools.run(name, args)
        assert out["demo"] is True and "error" not in out, name
        assert len(assistant._result_text(out)) <= assistant.MAX_RESULT_CHARS + 80
    rep = tools.run("stock_report", {"symbol": "DEMO03"})
    assert rep["symbol"] == "DEMO03" and rep["active_factors"] == [] and rep["paper_setup_long"]["valid"]
    assert tools.run("rankings", {"preview": False, "limit": 5})["bullish"] == []
    assert "error" in tools.run("stock_report", {"symbol": "NOPE"})
    assert tools.run("screen_stocks", {"sort_by": "rs_rank", "ascending": False, "industry": None, "limit": 999})["rows"]


def test_tool_schemas_are_strict_compatible():
    def walk(s):
        if isinstance(s, dict):
            assert not {"minimum", "maximum", "minLength", "maxLength"} & set(s)
            if s.get("type") == "object":
                assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for v in s.values():
                walk(v)
        elif isinstance(s, list):
            for v in s:
                walk(v)
    for t in assistant.TOOLS:
        walk(t["input_schema"])


def test_loop_runs_tools_and_returns_grounded_answer(tools):
    fake = FakeClient([
        NS(stop_reason="tool_use", model="claude-opus-5-5", usage=usage(),
           content=[text("Checking."), tool_use("t1", "stock_report", {"symbol": "DEMO01"}),
                    tool_use("t2", "data_status", {})]),
        NS(stop_reason="end_turn", model="claude-opus-5-5", usage=usage(), content=[text("Demo data: DEMO01 RS rank …")]),
    ])
    res = assistant.ask("How is DEMO01 doing?", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
                        tools, fake)
    assert res["answer"].startswith("Demo data") and [c["name"] for c in res["tool_calls"]] == ["stock_report", "data_status"]
    assert res["usage"]["input_tokens"] == 20 and res["usage"]["cache_read_input_tokens"] == 6
    first, second = fake.requests
    assert first["model"] == "claude-opus-5-5" and first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["fallbacks"] == "default" and all(t["strict"] for t in first["tools"])
    assert [m["role"] for m in first["messages"]] == ["user", "assistant", "user"]
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"], "all results in one user message"
    assert json.loads(results[0]["content"])["symbol"] == "DEMO01"


def test_tool_errors_are_reported_not_raised(tools):
    fake = FakeClient([
        NS(stop_reason="tool_use", usage=usage(), model="m", content=[tool_use("t1", "no_such_tool", {})]),
        NS(stop_reason="end_turn", usage=usage(), model="m", content=[text("Sorry, that failed.")]),
    ])
    res = assistant.ask("x", None, tools, fake)
    err = fake.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] and "unknown tool" in err["content"] and res["tool_calls"][0]["ok"] is False


def test_refusal_and_round_limit(tools):
    fake = FakeClient([NS(stop_reason="refusal", usage=usage(), model="m", content=[],
                          stop_details=NS(category="cyber", explanation=""))])
    assert "declined" in assistant.ask("x", None, tools, fake)["answer"]
    loop = FakeClient([NS(stop_reason="tool_use", usage=usage(), model="m",
                          content=[tool_use(f"t{i}", "data_status", {})]) for i in range(assistant.MAX_TOOL_ROUNDS)])
    out = assistant.ask("x", None, tools, loop)
    assert "maximum number of tool calls" in out["answer"] and len(loop.requests) == assistant.MAX_TOOL_ROUNDS


def test_api_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    assert c.get("/api/intel/assistant/status").json()["configured"] is False
    r = c.post("/api/intel/assistant", json={"question": "hi"})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_system_prompt_states_the_grounding_rules():
    s = assistant.SYSTEM_PROMPT
    for phrase in ("Answer from tool results only", "never attribute it to a single stock",
                   'never "institutional buying"', "not investment advice", "demo: true",
                   "pass their event-study test"):
        assert phrase in s, phrase
