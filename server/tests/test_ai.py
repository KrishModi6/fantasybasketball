"""AI assistant: tools run against a real league analysis; the model is a scripted fake."""

from __future__ import annotations

import json
import os

os.environ.setdefault("FBB_SECRET_KEY", "test-secret-key-not-for-production-use-0123456789")

import pytest  # noqa: E402

from app.services.ai import AIError, ModelTurn, run_assistant, throttle  # noqa: E402
from app.services.ai_tools import ToolBox, ToolError  # noqa: E402
from app.services.analysis import LeagueAnalysis  # noqa: E402
from factories import make_snapshot  # noqa: E402


@pytest.fixture(scope="module")
def analysis() -> LeagueAnalysis:
    return LeagueAnalysis("test-league", 1, make_snapshot())


class FakeClient:
    """Plays back model turns and records what the agent sent."""

    def __init__(self, turns: list[ModelTurn]):
        self.turns = list(turns)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.turns.pop(0)

    def close(self):
        pass


def test_every_tool_returns_json(analysis):
    tb = ToolBox(analysis, my_team_id=1)
    calls = {
        "league_overview": {},
        "team_roster": {},
        "free_agents": {"sort_by": "games_next_7_days", "limit": 5},
        "waiver_recommendations": {},
        "player_details": {"player": "Player 3"},
        "evaluate_trade": {"other_team": "Team 2", "give": ["Player 1"], "get": ["Player 9"]},
        "evaluate_three_team_trade": {"moves": [{"player": "Player 1", "to_team": "Team 2"},
                                                {"player": "Player 9", "to_team": "Team 3"},
                                                {"player": "Player 17", "to_team": "Team 1"}]},
        "trade_ideas": {},
        "trade_offers": {"players": ["Player 9"]},
        "what_to_ask_for": {"players": ["Player 1"], "partner": "Team 2"},
        "three_team_trade_ideas": {},
        "simulate_matchup": {"week": 2},
        "league_news": {},
    }
    assert set(calls) == {s["name"] for s in ToolBox.schemas()}
    for name, args in calls.items():
        out = tb.call(name, args)
        json.dumps(out)  # must serialise for the model
    assert len(tb.call("free_agents", {"limit": 5})["players"]) == 5
    assert tb.call("league_overview", {})["teams"][0]["rank"] == 1


def test_three_team_trade_tool(analysis):
    tb = ToolBox(analysis, my_team_id=3)
    out = tb.call("evaluate_three_team_trade", {"moves": [{"player": "Player 1", "to_team": "Team 2"},
                                                          {"player": "Player 9", "to_team": "Team 3"},
                                                          {"player": "Player 17", "to_team": "Team 1"}]})
    assert [s["team"] for s in out["sides"]] == ["Team 3", "Team 1", "Team 2"]  # the user's team first
    assert out["moves"][0] == {"player": "Player 17", "from": "Team 3", "to": "Team 1"}
    with pytest.raises(ToolError):  # only two teams involved
        tb.call("evaluate_three_team_trade", {"moves": [{"player": "Player 1", "to_team": "Team 2"}]})


def test_ask_and_three_team_tools(analysis):
    tb = ToolBox(analysis, my_team_id=1)
    out = tb.call("what_to_ask_for", {"players": ["Player 1"], "max_get": 2})
    assert out["give"] == ["Player 1"]
    assert all(len(o["get"]) <= 2 and o["from_team"] != "Team 1" for o in out["asks"] + out["closest_refused_asks"])
    with pytest.raises(ToolError, match="Pick one of your own players"):
        tb.call("what_to_ask_for", {"players": ["Player 9"]})
    ideas = tb.call("three_team_trade_ideas", {"partner": "Team 2"})["ideas"]
    assert all(len(i["moves"]) == 3 and "Team 2" in {s["team"] for s in i["partners"]} for i in ideas)
    with pytest.raises(ToolError):
        tb.call("three_team_trade_ideas", {"partner": "Team 1"})


def test_name_resolution(analysis):
    tb = ToolBox(analysis, my_team_id=None)
    assert tb.player_id("player 12") == "12"
    with pytest.raises(ToolError):
        tb.player_id("Nobody Real")
    with pytest.raises(ToolError):
        tb.team_id(None)  # no claimed team and none given
    assert tb.team_id("team 3") == 3


def test_agent_runs_tools_then_answers(analysis):
    throttle.hits.clear()
    fake = FakeClient([
        ModelTurn("r1", "", [{"call_id": "c1", "name": "waiver_recommendations", "arguments": "{}"},
                             {"call_id": "c2", "name": "player_details", "arguments": json.dumps({"player": "Nobody"})}]),
        ModelTurn("r2", "**Add Free 30.** It adds the most points per week."),
    ])
    out = run_assistant(analysis, user_id="u1", my_team_id=1, prompt="Best pickups?", client=fake)
    assert out["answer"].startswith("**Add")
    assert [t["name"] for t in out["tools_used"]] == ["waiver_recommendations", "player_details"]
    assert out["tools_used"][1]["ok"] is False  # unknown player is reported back, not raised
    follow_up = fake.requests[1]
    assert follow_up["previous_response_id"] == "r1"
    sent = {o["call_id"]: json.loads(o["output"]) for o in follow_up["input"]}
    assert "add_drop_rest_of_season" in sent["c1"] and "error" in sent["c2"]
    assert "Team 1" in fake.requests[0]["instructions"]  # the user's team is in the context


def test_assistant_rate_limit(analysis):
    throttle.hits.clear()
    from app.config import get_settings

    limit = get_settings().ai_user_hourly_limit
    for _ in range(limit):
        assert throttle.allow("busy", limit)
    with pytest.raises(AIError) as exc:
        run_assistant(analysis, user_id="busy", my_team_id=1, prompt="hi", client=FakeClient([]))
    assert exc.value.status == 429


def test_users_own_key_goes_straight_to_openai():
    import httpx

    from app.services.ai import OPENAI_URL, ResponsesClient, ai_mode

    assert ai_mode("sk-user-key") == "direct"
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
        return httpx.Response(200, json={"id": "r1", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Hi"}]}]})

    client = ResponsesClient("device", api_key="sk-user-key")
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    turn = client.create(input=[{"role": "user", "content": "hi"}], instructions="x", tools=[], previous_response_id=None)
    assert turn.text == "Hi"
    assert seen["url"] == OPENAI_URL and seen["auth"] == "Bearer sk-user-key"
    assert seen["body"]["model"]  # direct mode names the model itself


def test_own_key_is_not_rationed(analysis):
    from app.config import get_settings

    throttle.hits.clear()
    for _ in range(get_settings().ai_user_hourly_limit):
        assert throttle.allow("keyholder", get_settings().ai_user_hourly_limit)
    out = run_assistant(analysis, user_id="keyholder", my_team_id=1, prompt="hi", api_key="sk-own",
                        client=FakeClient([ModelTurn("r1", "Sure.")]))
    assert out["answer"] == "Sure."  # the shared hourly cap doesn't apply to their own key
