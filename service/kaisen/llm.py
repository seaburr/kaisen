"""Model client interface, the Anthropic implementation and a scripted fake for tests."""
import inspect
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import anthropic

SYSTEM = (Path(__file__).parent / "prompts" / "builder.md").read_text()

TOOLS = [
    {"name": "list_files", "description": "List all files in the project with sizes.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_file", "description": "Read one project file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Create or overwrite a project file with complete content.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "delete_file", "description": "Delete a project file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "ask_human", "description": "Ask the human one question and wait for the answer.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "finish", "description": "Declare the project complete, with a short summary.",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]


class ModelError(Exception):
    """Non-retryable (or retries exhausted) model failure. Message is safe to show."""


@dataclass
class ModelResponse:
    content: list  # list[dict], stored verbatim (thinking blocks included)
    stop_reason: str
    usage: dict = field(default_factory=dict)


class ModelClient(Protocol):
    def create(self, *, model: str, messages: list, effort: str) -> ModelResponse: ...
    def retrieve_model(self, model: str) -> str: ...


class AnthropicClient:
    def __init__(self, api_key: str = "", client=None, sleep: Callable[[float], None] = time.sleep):
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=600.0)
        self.sleep = sleep
        try:
            self._native_effort = "output_config" in inspect.signature(self.client.messages.stream).parameters
        except (TypeError, ValueError):
            self._native_effort = False

    def _call(self, model, messages, effort) -> ModelResponse:
        kw = dict(
            model=model, max_tokens=32000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS, messages=messages,
        )
        if self._native_effort:
            kw["output_config"] = {"effort": effort}
        else:
            kw["extra_body"] = {"output_config": {"effort": effort}}
        with self.client.messages.stream(**kw) as stream:
            msg = stream.get_final_message()
        u = msg.usage
        usage = {k: getattr(u, k, 0) or 0 for k in
                 ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
        content = [b.model_dump(mode="json", exclude_none=True) for b in msg.content]
        return ModelResponse(content=content, stop_reason=msg.stop_reason, usage=usage)

    def create(self, *, model: str, messages: list, effort: str) -> ModelResponse:
        delay = 2.0
        for attempt in range(5):
            try:
                return self._call(model, messages, effort)
            except anthropic.RateLimitError:
                err = "Anthropic rate limit hit"
            except anthropic.APIStatusError as e:
                if e.status_code < 500:
                    raise ModelError(f"Anthropic API error {e.status_code}: {_short(e)}") from None
                err = f"Anthropic server error {e.status_code}"
            except anthropic.APIConnectionError:
                err = "Could not reach the Anthropic API"
            if attempt == 4:
                raise ModelError(err + " (gave up after 4 retries)")
            self.sleep(delay)
            delay = min(delay * 2, 60)
        raise AssertionError("unreachable")

    def retrieve_model(self, model: str) -> str:
        try:
            m = self.client.models.retrieve(model)
            return getattr(m, "id", model)
        except anthropic.AuthenticationError:
            raise ModelError("Anthropic rejected the API key (401)") from None
        except anthropic.NotFoundError:
            raise ModelError(f"Model {model} not found for this key") from None
        except anthropic.APIError as e:
            raise ModelError(f"Anthropic API error: {_short(e)}") from None


def _short(e) -> str:
    return str(getattr(e, "message", e))[:200]


# ---- test helpers ----------------------------------------------------------

def tool_use(name: str, input: dict, id: str | None = None) -> dict:
    return {"type": "tool_use", "id": id or f"toolu_{name}_{abs(hash(str(input))) % 10**6}", "name": name, "input": input}


def reply(*blocks: dict, stop_reason: str | None = None, usage: dict | None = None) -> dict:
    if stop_reason is None:
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return {"content": list(blocks), "stop_reason": stop_reason,
            "usage": usage or {"input_tokens": 1000, "output_tokens": 500,
                               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


class FakeModelClient:
    """script items: dict from reply(), an Exception to raise, or callable(messages)->dict."""

    def __init__(self, script=(), model_ok: bool = True):
        self.script = list(script)
        self.calls: list[list] = []
        self.model_ok = model_ok

    def create(self, *, model: str, messages: list, effort: str) -> ModelResponse:
        self.calls.append(list(messages))
        if not self.script:
            raise ModelError("fake script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(messages)
        return ModelResponse(content=item["content"], stop_reason=item["stop_reason"], usage=item["usage"])

    def retrieve_model(self, model: str) -> str:
        if not self.model_ok:
            raise ModelError("Anthropic rejected the API key (401)")
        return model
