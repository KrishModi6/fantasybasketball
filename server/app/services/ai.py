"""AI assistant: an OpenAI Responses-API agent that answers with Courtside's own numbers.

Two transports, chosen by configuration:
  direct  FBB_OPENAI_API_KEY is set (a hosted server holding its own key)
  proxy   FBB_AI_PROXY_URL + FBB_AI_PROXY_TOKEN (the desktop app; the key lives only in the
          Cloudflare Worker in cloudflare/ai-proxy, which also enforces rate limits)

The model never sees raw tables up front. It calls the tools in ai_tools.py, which run the
projection engine, lineup simulation, trade evaluator and matchup simulator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import get_settings
from .ai_tools import ToolBox, ToolError
from .analysis import LeagueAnalysis

log = logging.getLogger(__name__)
OPENAI_URL = "https://api.openai.com/v1/responses"
TOOL_OUTPUT_LIMIT = 14_000  # characters per tool result sent back to the model


class AIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class ModelTurn:
    response_id: str
    text: str
    calls: list[dict] = field(default_factory=list)  # {call_id, name, arguments}
    quota: dict | None = None  # proxy only: {device_used, device_limit, resets_at}


def ai_mode() -> str:
    s = get_settings()
    if s.openai_api_key:
        return "direct"
    if s.ai_proxy_url and s.ai_proxy_token:
        return "proxy"
    return "off"


class ResponsesClient:
    """Minimal OpenAI Responses API client (direct, or through the Courtside AI proxy)."""

    def __init__(self, device_id: str, timeout: float = 120.0):
        self.s = get_settings()
        self.mode = ai_mode()
        self.device_id = device_id
        self.http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def create(self, *, input: Any, instructions: str, tools: list[dict], previous_response_id: str | None) -> ModelTurn:
        body: dict[str, Any] = {"input": input, "instructions": instructions, "tools": tools}
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        if self.mode == "direct":
            body.update({"model": self.s.ai_model, "reasoning": {"effort": "low"}, "max_output_tokens": 3000})
            url, headers = OPENAI_URL, {"Authorization": f"Bearer {self.s.openai_api_key}"}
        elif self.mode == "proxy":
            url = self.s.ai_proxy_url.rstrip("/") + "/v1/responses"
            headers = {"Authorization": f"Bearer {self.s.ai_proxy_token}", "X-Device-Id": self.device_id}
        else:
            raise AIError(503, "AI isn't set up on this server. See docs/AI.md.")
        try:
            r = self.http.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIError(504, "The AI service took too long. Try again.") from exc
        except httpx.HTTPError as exc:
            raise AIError(502, "Couldn't reach the AI service. Check your internet connection.") from exc
        if r.status_code >= 400:
            raise AIError(r.status_code if r.status_code in (400, 401, 429, 503, 504) else 502, _error_message(r))
        data = r.json()
        turn = ModelTurn(response_id=data.get("id", ""), text="", quota=data.get("quota"))
        texts = []
        for item in data.get("output", []):
            if item.get("type") == "function_call":
                turn.calls.append({"call_id": item["call_id"], "name": item["name"], "arguments": item.get("arguments") or "{}"})
            elif item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        texts.append(c.get("text", ""))
        turn.text = "\n".join(t for t in texts if t).strip()
        return turn


def _error_message(r: httpx.Response) -> str:
    try:
        data = r.json()
        msg = data.get("error")
        if isinstance(msg, dict):
            msg = msg.get("message")
    except ValueError:
        msg = None
    if r.status_code == 429:
        return msg or "The AI usage limit was reached. Try again later."
    if r.status_code == 401:
        return msg or "The AI service rejected this app's access token."
    return msg or f"The AI service returned an error ({r.status_code})."


SYSTEM = """You are Courtside's fantasy basketball analyst for the league described below.

Rules:
- Use the tools to get facts. Every number you state must come from a tool result; never estimate or invent stats, projections, odds or schedules.
- Courtside's projections are statistics-first. News headlines are context only; mention them, but don't let them override the numbers.
- When recommending moves, say what to do, why (cite the specific numbers: points per week gained, games this week, win probability change), and the main risk.
- Prefer the engine's simulated values (waiver_recommendations, evaluate_trade, simulate_matchup) over your own reasoning about player value.
- Be concise and practical. Use short markdown: a one-line bottom line first, then bullets or a small table. No preamble.
- If the user hasn't claimed a team and a tool needs one, ask which team is theirs.
"""

PRESETS: dict[str, str] = {
    "waivers": "Who are the best waiver pickups for my team right now? Give me the top moves for the rest of the season and "
               "the best streams for this week, who to drop for each, and how many points per week each adds.",
    "matchup": "Give me a game plan for my matchup this week: my win probability, what decides it, which players swing it, "
               "and any lineup or streaming moves that would raise my odds.",
    "trades": "Find realistic trades that improve my team. For the best two, explain why the numbers favour me, why the other "
              "manager might accept, and write a short message I could send them.",
    "injuries": "Check my roster for injuries and risky players. For each, how many games are they expected to miss, how much "
                "does it cost me per week, and who on waivers best covers it?",
    "recap": "Write fun, punchy power-rankings blurbs for every team in the league (one or two sentences each), based on the "
             "rankings, records and playoff odds. Keep it friendly trash talk.",
    "outlook": "Give me my team's outlook for the rest of the season: where I rank, my playoff and title odds, my biggest "
               "strengths and weaknesses by stat, and the single most valuable move I could make.",
}


class Throttle:
    """Per-user request limit (in memory), on top of the proxy's own limits."""

    def __init__(self) -> None:
        self.hits: dict[str, list[float]] = defaultdict(list)
        self.lock = threading.Lock()

    def allow(self, key: str, per_hour: int) -> bool:
        now = time.time()
        with self.lock:
            hits = [t for t in self.hits[key] if now - t < 3600]
            ok = len(hits) < per_hour
            if ok:
                hits.append(now)
            self.hits[key] = hits
            return ok


throttle = Throttle()


def device_id_for(user_id: str) -> str:
    """Stable, non-identifying id the proxy uses for per-device limits."""
    salt = hashlib.sha256(get_settings().signing_key().encode()).hexdigest()[:16]
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"courtside:{salt}:{user_id}"))


def _context(a: LeagueAnalysis, my_team_id: int | None) -> str:
    s = a.snapshot.settings
    today = a.snapshot.schedule.period_dates.get(a.snapshot.current_period)
    scoring = (", ".join(f"{k} {v:+g}" for k, v in s.scoring.points.items()) if s.scoring.kind == "points"
               else "categories: " + ", ".join(s.scoring.categories))
    mine = a.snapshot.team(my_team_id).name if my_team_id is not None else "not claimed yet"
    slots = ", ".join(f"{k}x{v}" if v > 1 else k for k, v in s.roster_slots.items())
    return (f"League: {s.name}, {s.team_count} teams, season {s.season - 1}-{str(s.season)[2:]}. Today: {today}, "
            f"week {a.snapshot.current_matchup_period} of {s.regular_season_matchups} (then {s.playoff_teams}-team playoffs). "
            f"Scoring: {scoring}. Lineup slots: {slots}; daily lineups. "
            f"Adds per matchup: {s.acquisition_limit_per_matchup or 'unlimited'}. The user's team: {mine}"
            + (f" (team id {my_team_id})." if my_team_id is not None else "."))


def run_assistant(a: LeagueAnalysis, *, user_id: str, my_team_id: int | None, prompt: str,
                  previous_response_id: str | None = None, client: ResponsesClient | None = None) -> dict:
    s = get_settings()
    if not throttle.allow(user_id, s.ai_user_hourly_limit):
        raise AIError(429, f"You've used the assistant {s.ai_user_hourly_limit} times this hour. Try again later.")
    tools = ToolBox(a, my_team_id)
    schemas = ToolBox.schemas()
    instructions = SYSTEM + "\n" + _context(a, my_team_id)
    own_client = client is None
    client = client or ResponsesClient(device_id_for(user_id))
    used: list[dict] = []
    started = time.monotonic()
    try:
        turn = client.create(input=[{"role": "user", "content": prompt}], instructions=instructions, tools=schemas,
                             previous_response_id=previous_response_id)
        for _ in range(s.ai_max_rounds):
            if not turn.calls:
                break
            outputs = []
            for call in turn.calls:
                args: dict = {}
                ok = False
                try:
                    args = json.loads(call["arguments"] or "{}")
                    result = tools.call(call["name"], args)
                    ok = True
                except ToolError as exc:
                    result = {"error": str(exc)}
                except (TypeError, json.JSONDecodeError) as exc:
                    result = {"error": f"Bad arguments: {exc}"}
                except Exception as exc:  # noqa: BLE001 - report to the model rather than fail the whole answer
                    log.exception("AI tool %s failed", call["name"])
                    result = {"error": f"{call['name']} failed: {exc}"}
                used.append({"name": call["name"], "args": args, "ok": ok})
                outputs.append({"type": "function_call_output", "call_id": call["call_id"],
                                "output": json.dumps(result, default=str)[:TOOL_OUTPUT_LIMIT]})
            turn = client.create(input=outputs, instructions=instructions, tools=schemas,
                                 previous_response_id=turn.response_id)
        else:
            if turn.calls:
                raise AIError(502, "The assistant needed too many steps. Try a narrower question.")
    finally:
        if own_client:
            client.close()
    return {"answer": turn.text or "The assistant didn't return an answer. Try rephrasing.",
            "response_id": turn.response_id, "tools_used": used, "seconds": round(time.monotonic() - started, 1),
            "quota": turn.quota}
