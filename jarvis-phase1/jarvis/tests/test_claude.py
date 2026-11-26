import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from jarvis_core.claude import SYSTEM_PROMPT, ClaudeNotConfiguredError, ClaudeService, ClaudeUnavailableError
from jarvis_core.config import Settings

REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def conn_err():
    return anthropic.APIConnectionError(request=REQ)


def auth_err():
    return anthropic.AuthenticationError("bad", response=httpx.Response(401, request=REQ), body=None)


class FakeStream:
    def __init__(self, parts, fail_after=None):
        self.parts, self.fail_after = parts, fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def gen():
            for i, p in enumerate(self.parts):
                if self.fail_after is not None and i == self.fail_after:
                    raise conn_err()
                yield p
        return gen()


class FakeClient:
    def __init__(self, script):
        self.script, self.kwargs = list(script), []
        self.messages = self

    async def create(self, **kw):
        self.kwargs.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=item)])

    def stream(self, **kw):
        self.kwargs.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def svc(client, key="k", retries=3):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    s = ClaudeService(Settings(claude_max_retries=retries), lambda: key, lambda k: client, sleep=fake_sleep)
    s.sleeps = sleeps
    return s


MSGS = [{"role": "user", "content": "hi"}]


def run(coro):
    return asyncio.run(coro)


def test_system_prompt_addresses_boss():
    assert '"Boss"' in SYSTEM_PROMPT


def test_complete_injects_memory_and_boss_prompt():
    c = FakeClient(["Hello Boss"])
    out = run(svc(c).complete(MSGS, "opus", "<memory>x</memory>"))
    assert out == "Hello Boss"
    kw = c.kwargs[0]
    assert "Boss" in kw["system"] and "<memory>x</memory>" in kw["system"]
    assert kw["model"] == "claude-opus-5-5"


def test_retries_with_exponential_backoff_then_succeeds():
    c = FakeClient([conn_err(), conn_err(), "ok"])
    s = svc(c)
    assert run(s.complete(MSGS)) == "ok"
    assert len(s.sleeps) == 2 and s.sleeps[1] > 0
    assert s.health()["status"] == "online"


def test_gives_up_after_max_retries():
    c = FakeClient([conn_err()] * 4)
    s = svc(c, retries=3)
    with pytest.raises(ClaudeUnavailableError):
        run(s.complete(MSGS))
    assert len(c.kwargs) == 4 and s.health()["status"] == "degraded"


def test_auth_error_not_retried_and_key_not_leaked():
    c = FakeClient([auth_err(), "never"])
    s = svc(c, key="sk-ant-SECRET")
    with pytest.raises(ClaudeUnavailableError) as e:
        run(s.complete(MSGS))
    assert len(c.kwargs) == 1 and "SECRET" not in str(e.value)


def test_not_configured():
    s = svc(FakeClient([]), key=None)
    assert s.health()["status"] == "offline"
    with pytest.raises(ClaudeNotConfiguredError):
        run(s.complete(MSGS))


def test_rejects_conversation_not_starting_with_user():
    with pytest.raises(ValueError):
        run(svc(FakeClient(["x"])).complete([{"role": "assistant", "content": "x"}]))


def test_stream_yields_chunks():
    async def go():
        return [t async for t in svc(FakeClient([FakeStream(["a", "b"])])).stream(MSGS)]
    assert run(go()) == ["a", "b"]


def test_stream_retries_only_before_first_token():
    async def go():
        s = svc(FakeClient([conn_err(), FakeStream(["a", "b"])]))
        return [t async for t in s.stream(MSGS)]
    assert run(go()) == ["a", "b"]


def test_stream_does_not_retry_after_output_started():
    c = FakeClient([FakeStream(["a", "b"], fail_after=1), FakeStream(["a", "b"])])

    async def go():
        got = []
        with pytest.raises(ClaudeUnavailableError):
            async for t in svc(c).stream(MSGS):
                got.append(t)
        return got
    assert run(go()) == ["a"] and len(c.kwargs) == 1


def test_timeout_is_enforced():
    class Slow(FakeClient):
        async def create(self, **kw):
            await asyncio.sleep(10)

    s = ClaudeService(Settings(claude_timeout_s=0.01, claude_max_retries=0), lambda: "k", lambda k: Slow([]))
    s._s = Settings(claude_timeout_s=-4.99, claude_max_retries=0)  # wait_for = timeout + 5 => 0.01s
    with pytest.raises(ClaudeUnavailableError):
        run(s.complete(MSGS))
