import asyncio
import json

import anthropic
import httpx2 as httpx

from bot.ai_check import AICheck
from bot.models import Listing


def _check(reply: dict, seen: list) -> AICheck:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(reply)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
    ai = AICheck.__new__(AICheck)
    ai.anthropic = anthropic
    ai.client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return ai


def _listing(desc: str) -> Listing:
    return Listing(platform="wallapop", item_id="1", title="MacBook Air M1", price=300,
                   url="https://es.wallapop.com/item/1", description=desc)


def test_ai_rejects_defect():
    seen = []
    ai = _check({"perfecto": False, "motivo": "hay que cambiar la pantalla"}, seen)
    ok, why = asyncio.run(ai.review(_listing("la pantalla tiene un fallo raro"), {"name": "MacBook Air M1"}))
    assert not ok and "pantalla" in why
    body = seen[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["effort"] == "low"
    assert "la pantalla tiene un fallo raro" in body["messages"][0]["content"]


def test_ai_accepts_perfect():
    ai = _check({"perfecto": True, "motivo": "sin problemas mencionados"}, [])
    assert asyncio.run(ai.review(_listing("Como nuevo, con caja"), {"name": "MacBook Air M1"}))[0]


def test_ai_disabled_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AICheck.from_env() is None
