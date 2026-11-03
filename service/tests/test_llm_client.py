"""AnthropicClient retry/error mapping against an in-process fake SDK client (no network)."""
import httpx
import anthropic
import pytest

from kaisen.llm import AnthropicClient, ModelError


class _Usage:
    input_tokens = 3
    output_tokens = 4
    cache_creation_input_tokens = None
    cache_read_input_tokens = 1


class _Block:
    def model_dump(self, mode, exclude_none):
        return {"type": "text", "text": "hi"}


class _Msg:
    usage = _Usage()
    content = [_Block()]
    stop_reason = "end_turn"


class _Stream:
    def __init__(self, outcome):
        self.outcome = outcome

    def __enter__(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return _Msg()


class _Messages:
    def __init__(self, outcomes):
        self.outcomes, self.kw = list(outcomes), []

    def stream(self, *, model, max_tokens, system, tools, messages, extra_body=None):
        self.kw.append(dict(extra_body=extra_body))
        return _Stream(self.outcomes.pop(0))


class _Models:
    def __init__(self, result):
        self.result = result

    def retrieve(self, model):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Sdk:
    def __init__(self, outcomes=(), model=None):
        self.messages = _Messages(outcomes)
        self.models = _Models(model)


def _resp(code):
    return httpx.Response(code, request=httpx.Request("POST", "https://x"))


def _status(code):
    return anthropic.APIStatusError("nope", response=_resp(code), body=None)


def _client(outcomes=(), model=None):
    sleeps = []
    sdk = _Sdk(outcomes, model)
    return AnthropicClient(client=sdk, sleep=sleeps.append), sdk, sleeps


def test_success_maps_usage_and_effort_via_extra_body():
    c, sdk, _ = _client([None])
    r = c.create(model="m", messages=[], effort="low")
    assert r.stop_reason == "end_turn" and r.usage["cache_creation_input_tokens"] == 0
    assert r.usage["input_tokens"] == 3
    assert sdk.messages.kw[0]["extra_body"] == {"output_config": {"effort": "low"}}


def test_server_error_retries_with_backoff_then_succeeds():
    c, _, sleeps = _client([_status(503), anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), None])
    assert c.create(model="m", messages=[], effort="low").content[0]["text"] == "hi"
    assert sleeps == [2.0, 4.0]


def test_client_error_is_not_retried():
    c, _, sleeps = _client([_status(400)])
    with pytest.raises(ModelError, match="400"):
        c.create(model="m", messages=[], effort="low")
    assert sleeps == []


def test_gives_up_after_retries():
    c, _, sleeps = _client([_status(500)] * 5)
    with pytest.raises(ModelError, match="gave up"):
        c.create(model="m", messages=[], effort="low")
    assert len(sleeps) == 4


def test_retrieve_model_error_mapping():
    class Obj:
        id = "claude-x"
    assert _client(model=Obj())[0].retrieve_model("m") == "claude-x"
    r = _resp(401)
    with pytest.raises(ModelError, match="401"):
        _client(model=anthropic.AuthenticationError("x", response=r, body=None))[0].retrieve_model("m")
    with pytest.raises(ModelError, match="not found"):
        _client(model=anthropic.NotFoundError("x", response=_resp(404), body=None))[0].retrieve_model("m")
    with pytest.raises(ModelError, match="API error"):
        _client(model=anthropic.APIConnectionError(request=httpx.Request("GET", "https://x")))[0].retrieve_model("m")
