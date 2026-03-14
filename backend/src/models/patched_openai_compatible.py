"""Patched OpenAI-compatible ``chat.completions`` model adapter.

This patch keeps the generic ``ChatOpenAI`` request/response flow, while adding
two vendor-compatibility behaviors that show up frequently on OpenAI-compatible
gateways:

1. Preserve assistant ``reasoning_content`` across multi-turn requests.
2. Optionally encode ``reasoning_effort`` into the model name suffix instead of
   sending it as a request parameter.

The suffix behavior is disabled by default so the adapter stays generic. Enable
it per model config only for vendors that actually require model names like
``base-model(high)``. When thinking is explicitly disabled, the adapter defaults
to ``reasoning_effort="none"`` because several OpenAI-compatible gateways
accept that shape while rejecting the internal ``minimal`` value used elsewhere
in the app.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def _is_disabled_thinking(thinking: Any) -> bool:
    return isinstance(thinking, dict) and thinking.get("type") == "disabled"


class PatchedOpenAICompatibleChatModel(ChatOpenAI):
    """Generic OpenAI-compatible ``chat.completions`` adapter."""

    reasoning_effort_as_model_suffix: bool = False
    reasoning_effort_model_suffixes: tuple[str, ...] = ("low", "medium", "high")
    reasoning_effort_model_suffix_format: str = "({reasoning_effort})"
    reasoning_effort_model_base_name: str | None = None
    thinking_disabled_reasoning_effort: str | None = "none"

    @staticmethod
    def _extract_reasoning_content_from_response_message(message_dict: dict[str, Any]) -> str | None:
        """Normalize vendor reasoning fields to ``reasoning_content``."""
        if isinstance(message_dict.get("reasoning_content"), str):
            return message_dict["reasoning_content"]
        if isinstance(message_dict.get("reasoning"), str):
            return message_dict["reasoning"]
        if isinstance(message_dict.get("thinking"), str):
            return message_dict["thinking"]
        return None

    def _render_reasoning_suffix(self, reasoning_effort: str) -> str | None:
        try:
            return self.reasoning_effort_model_suffix_format.format(reasoning_effort=reasoning_effort)
        except Exception:
            logger.warning(
                "Invalid reasoning_effort_model_suffix_format=%r; expected a {reasoning_effort} placeholder",
                self.reasoning_effort_model_suffix_format,
            )
            return None

    def _resolve_base_model_name(self, raw_model: str) -> str:
        if self.reasoning_effort_model_base_name:
            return self.reasoning_effort_model_base_name

        base_model = raw_model.strip()
        for effort in sorted(set(self.reasoning_effort_model_suffixes), key=len, reverse=True):
            suffix = self._render_reasoning_suffix(effort)
            if suffix and base_model.endswith(suffix):
                return base_model[: -len(suffix)].rstrip()
        return base_model

    @staticmethod
    def _has_disabled_thinking(payload: dict[str, Any]) -> bool:
        extra_body = payload.get("extra_body")
        if isinstance(extra_body, dict) and _is_disabled_thinking(extra_body.get("thinking")):
            return True
        return _is_disabled_thinking(payload.get("thinking"))

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Build request payload with optional suffix rewriting."""
        original_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        payload_messages = payload.get("messages", [])
        if isinstance(payload_messages, list):
            if len(payload_messages) == len(original_messages):
                pairs = zip(payload_messages, original_messages)
            else:
                ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
                assistant_payloads = [m for m in payload_messages if m.get("role") == "assistant"]
                pairs = zip(assistant_payloads, ai_messages)

            for payload_msg, orig_msg in pairs:
                if payload_msg.get("role") != "assistant" or not isinstance(orig_msg, AIMessage):
                    continue
                reasoning_content = orig_msg.additional_kwargs.get("reasoning_content")
                if reasoning_content is not None:
                    payload_msg["reasoning_content"] = reasoning_content

        requested_effort = payload.get("reasoning_effort")
        thinking_disabled = self._has_disabled_thinking(payload)
        if thinking_disabled:
            if self.thinking_disabled_reasoning_effort is None:
                payload.pop("reasoning_effort", None)
            else:
                payload["reasoning_effort"] = self.thinking_disabled_reasoning_effort

        requested_effort = payload.get("reasoning_effort")
        supports_suffix = isinstance(requested_effort, str) and requested_effort in set(self.reasoning_effort_model_suffixes)

        if self.reasoning_effort_as_model_suffix and supports_suffix and not thinking_disabled:
            suffix = self._render_reasoning_suffix(requested_effort)
            if suffix:
                raw_model = str(payload.get("model") or self.model_name)
                base_model = self._resolve_base_model_name(raw_model)
                payload["model"] = f"{base_model}{suffix}"
                payload.pop("reasoning_effort", None)

        return payload

    def _create_chat_result(self, response: dict, generation_info: dict | None = None):
        """Attach vendor reasoning fields to ``AIMessage.additional_kwargs``."""
        result = super()._create_chat_result(response, generation_info=generation_info)

        try:
            response_dict = response if isinstance(response, dict) else response.model_dump()  # type: ignore[unreachable]
            choices = response_dict.get("choices") or []

            for i, choice in enumerate(choices):
                msg = (choice or {}).get("message") or {}
                if not isinstance(msg, dict):
                    continue

                reasoning_content = self._extract_reasoning_content_from_response_message(msg)
                if reasoning_content is None:
                    continue

                if i < len(result.generations) and isinstance(result.generations[i].message, AIMessage):
                    result.generations[i].message.additional_kwargs["reasoning_content"] = reasoning_content
        except Exception:
            logger.debug("Failed to attach reasoning_content to ChatResult", exc_info=True)

        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        """Attach streaming reasoning deltas to ``AIMessageChunk.additional_kwargs``."""
        generation_chunk = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation_chunk is None:
            return None

        try:
            choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
            if not choices:
                return generation_chunk

            delta = choices[0].get("delta") or {}
            if not isinstance(delta, dict):
                return generation_chunk

            reasoning_delta = None
            if isinstance(delta.get("reasoning_content"), str):
                reasoning_delta = delta["reasoning_content"]
            elif isinstance(delta.get("reasoning"), str):
                reasoning_delta = delta["reasoning"]
            elif isinstance(delta.get("thinking"), str):
                reasoning_delta = delta["thinking"]

            if reasoning_delta is None:
                return generation_chunk

            msg = generation_chunk.message
            if hasattr(msg, "additional_kwargs"):
                msg.additional_kwargs["reasoning_content"] = reasoning_delta
        except Exception:
            logger.debug("Failed to attach reasoning_content on streaming chunk", exc_info=True)

        return generation_chunk
