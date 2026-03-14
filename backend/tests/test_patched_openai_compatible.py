from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from src.models.patched_openai_compatible import PatchedOpenAICompatibleChatModel


def _make_model(**kwargs) -> PatchedOpenAICompatibleChatModel:
    return PatchedOpenAICompatibleChatModel(
        model="gpt-5.2",
        api_key="test-key",
        **kwargs,
    )


def test_request_payload_preserves_reasoning_content(monkeypatch):
    model = _make_model()
    original_messages = [
        HumanMessage(content="hello"),
        AIMessage(content="hi", additional_kwargs={"reasoning_content": "kept reasoning"}),
    ]

    monkeypatch.setattr(
        model,
        "_convert_input",
        lambda input_: SimpleNamespace(to_messages=lambda: original_messages),
    )
    monkeypatch.setattr(
        ChatOpenAI,
        "_get_request_payload",
        lambda self, input_, *, stop=None, **kwargs: {
            "model": "gpt-5.2",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "reasoning_effort": "medium",
        },
    )

    payload = model._get_request_payload("ignored")

    assert payload["messages"][1]["reasoning_content"] == "kept reasoning"
    assert payload["model"] == "gpt-5.2"
    assert payload["reasoning_effort"] == "medium"


def test_reasoning_effort_suffix_rewrite_is_optional(monkeypatch):
    model = _make_model(reasoning_effort_as_model_suffix=True)

    monkeypatch.setattr(
        model,
        "_convert_input",
        lambda input_: SimpleNamespace(to_messages=lambda: [HumanMessage(content="hello")]),
    )
    monkeypatch.setattr(
        ChatOpenAI,
        "_get_request_payload",
        lambda self, input_, *, stop=None, **kwargs: {
            "model": "gpt-5.2",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "high",
        },
    )

    payload = model._get_request_payload("ignored")

    assert payload["model"] == "gpt-5.2(high)"
    assert "reasoning_effort" not in payload


def test_disabled_thinking_does_not_map_to_model_suffix(monkeypatch):
    model = _make_model(reasoning_effort_as_model_suffix=True)

    monkeypatch.setattr(
        model,
        "_convert_input",
        lambda input_: SimpleNamespace(to_messages=lambda: [HumanMessage(content="hello")]),
    )
    monkeypatch.setattr(
        ChatOpenAI,
        "_get_request_payload",
        lambda self, input_, *, stop=None, **kwargs: {
            "model": "gpt-5.2",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "minimal",
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )

    payload = model._get_request_payload("ignored")

    assert payload["model"] == "gpt-5.2"
    assert payload["reasoning_effort"] == "none"
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}


def test_disabled_thinking_can_override_reasoning_effort(monkeypatch):
    model = _make_model(thinking_disabled_reasoning_effort="none")

    monkeypatch.setattr(
        model,
        "_convert_input",
        lambda input_: SimpleNamespace(to_messages=lambda: [HumanMessage(content="hello")]),
    )
    monkeypatch.setattr(
        ChatOpenAI,
        "_get_request_payload",
        lambda self, input_, *, stop=None, **kwargs: {
            "model": "gpt-5.2",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "minimal",
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )

    payload = model._get_request_payload("ignored")

    assert payload["reasoning_effort"] == "none"


def test_disabled_thinking_can_drop_reasoning_effort(monkeypatch):
    model = _make_model(thinking_disabled_reasoning_effort=None)

    monkeypatch.setattr(
        model,
        "_convert_input",
        lambda input_: SimpleNamespace(to_messages=lambda: [HumanMessage(content="hello")]),
    )
    monkeypatch.setattr(
        ChatOpenAI,
        "_get_request_payload",
        lambda self, input_, *, stop=None, **kwargs: {
            "model": "gpt-5.2",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "minimal",
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )

    payload = model._get_request_payload("ignored")

    assert "reasoning_effort" not in payload


def test_create_chat_result_attaches_reasoning_content(monkeypatch):
    model = _make_model()

    monkeypatch.setattr(
        ChatOpenAI,
        "_create_chat_result",
        lambda self, response, generation_info=None: ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="answer"))]
        ),
    )

    result = model._create_chat_result(
        {
            "choices": [
                {
                    "message": {
                        "content": "answer",
                        "reasoning_content": "visible reasoning",
                    }
                }
            ]
        }
    )

    assert result.generations[0].message.additional_kwargs["reasoning_content"] == "visible reasoning"


def test_streaming_chunk_attaches_reasoning_content(monkeypatch):
    model = _make_model()

    monkeypatch.setattr(
        ChatOpenAI,
        "_convert_chunk_to_generation_chunk",
        lambda self, chunk, default_chunk_class, base_generation_info: ChatGenerationChunk(
            message=AIMessageChunk(content="delta")
        ),
    )

    chunk = model._convert_chunk_to_generation_chunk(
        {"choices": [{"delta": {"reasoning_content": "stream reasoning"}}]},
        default_chunk_class=AIMessageChunk,
        base_generation_info=None,
    )

    assert chunk is not None
    assert chunk.message.additional_kwargs["reasoning_content"] == "stream reasoning"
