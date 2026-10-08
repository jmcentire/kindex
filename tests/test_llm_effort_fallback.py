"""OpenAI models differ in the reasoning efforts they take; a rejected effort is
retried one step lower (or without one) and the model's limit is remembered."""
import io
import json
import urllib.error

import pytest

from kindex import llm


def _rejection(code):
    body = json.dumps({"error": {"type": "invalid_request_error", "param": "reasoning.effort", "code": code,
                                 "message": "echoed prompt text that must never be shown"}}).encode()
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", 400, "Bad Request", {}, io.BytesIO(body))


class _Reply:
    def __init__(self):
        self.body = json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
                                "usage": {"input_tokens": 3, "output_tokens": 1}}).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch):
    monkeypatch.setattr(llm, "_EFFORT_LIMITS", {})


def _fake_api(monkeypatch, accepts):
    """accepts: the efforts the fake model takes, or None for a model that takes no effort."""
    sent = []

    def urlopen(request, timeout=None):
        payload = json.loads(request.data)
        effort = (payload.get("reasoning") or {}).get("effort")
        sent.append(effort)
        if effort is not None and accepts is None:
            raise _rejection("unsupported_parameter")
        if effort is not None and effort not in accepts:
            raise _rejection("unsupported_value")
        return _Reply()

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    return sent


def _ask(effort, model="gpt-5-mini"):
    out = llm._OpenAIResponsesMessages("key").create(
        model=model, max_tokens=50, messages=[{"role": "user", "content": "hi"}], reasoning_effort=effort)
    return out.content[0].text


def test_an_unsupported_effort_steps_down_and_is_remembered(monkeypatch):
    sent = _fake_api(monkeypatch, accepts={"minimal", "low", "medium", "high"})
    assert _ask("xhigh") == "OK"
    assert sent == ["xhigh", "high"]
    sent.clear()
    assert _ask("xhigh") == "OK" and sent == ["high"]      # starts from the remembered limit
    sent.clear()
    assert _ask("medium") == "OK" and sent == ["medium"]   # lower efforts are untouched


def test_a_model_without_reasoning_is_asked_without_an_effort(monkeypatch):
    sent = _fake_api(monkeypatch, accepts=None)
    assert _ask("medium", model="gpt-4o-mini") == "OK"
    assert sent == ["medium", None]
    sent.clear()
    assert _ask("high", model="gpt-4o-mini") == "OK" and sent == [None]


def test_the_lowest_rejected_effort_falls_back_to_none(monkeypatch):
    sent = _fake_api(monkeypatch, accepts={"low", "medium", "high"})
    assert _ask("minimal", model="o4-mini") == "OK"
    assert sent == ["minimal", None]


def test_other_bad_requests_still_fail_without_echoing_the_body(monkeypatch):
    def urlopen(request, timeout=None):
        body = json.dumps({"error": {"param": "input", "code": "invalid_value", "message": "secret prompt"}}).encode()
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError) as err:
        _ask("high")
    assert "400" in str(err.value) and "secret" not in str(err.value)


def test_a_supported_effort_is_sent_unchanged(monkeypatch):
    sent = _fake_api(monkeypatch, accepts={"medium", "high", "xhigh"})
    assert _ask("xhigh", model="gpt-6-luna") == "OK" and sent == ["xhigh"]
