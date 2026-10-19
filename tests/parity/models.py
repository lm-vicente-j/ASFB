from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

EFFORT = os.environ.get("ASFB_EFFORT", "high")


@dataclass(frozen=True)
class ModelSpec:
    key: str
    display: str
    provider: str  # "openai" | "anthropic" | "google"
    model_env: str
    default_id: str | None
    key_env: str

    def model_id(self) -> str | None:
        return os.environ.get(self.model_env) or self.default_id


MODELS = [
    ModelSpec("gpt-6.1-sol", "GPT-6.1 Sol", "openai", "ASFB_MODEL_GPT", "gpt-6.1-sol", "OPENAI_API_KEY"),
    ModelSpec("claude-opus-5.5", "Claude Opus 5.5", "anthropic", "ASFB_MODEL_CLAUDE", "claude-opus-5-5", "ANTHROPIC_API_KEY"),
    # No public API id for Gemini 4 Argon yet, so it must be set via env.
    ModelSpec("gemini-4-argon", "Gemini 4 Argon", "google", "ASFB_MODEL_GEMINI", None, "GEMINI_API_KEY"),
]


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelUnavailable(Exception):
    pass


# (system, user) -> Completion
Complete = Callable[[str, str], Completion]


def get_client(spec: ModelSpec) -> Complete:
    mid = spec.model_id()
    if mid is None:
        raise ModelUnavailable(f"{spec.display}: no model id; set {spec.model_env}")
    if not os.environ.get(spec.key_env):
        raise ModelUnavailable(f"{spec.display}: {spec.key_env} is not set")

    if spec.provider == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise ModelUnavailable(f"{spec.display}: anthropic SDK not installed ({exc})") from exc
        return _anthropic_complete(anthropic.Anthropic(), mid)
    if spec.provider == "openai":
        try:
            import openai
        except ImportError as exc:
            raise ModelUnavailable(f"{spec.display}: openai SDK not installed ({exc})") from exc
        return _openai_complete(openai.OpenAI(), mid)
    if spec.provider == "google":
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ModelUnavailable(f"{spec.display}: google-genai SDK not installed ({exc})") from exc
        return _google_complete(genai.Client(), types, mid)
    raise ModelUnavailable(f"{spec.display}: unknown provider {spec.provider!r}")


def _anthropic_complete(client, mid: str) -> Complete:
    def complete(system: str, user: str) -> Completion:
        # No server-side fallbacks: a different model answering would contaminate the measurement.
        # No temperature: rejected (400) by this model.
        with client.messages.stream(
            model=mid,
            max_tokens=64000,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
        ) as stream:
            msg = stream.get_final_message()
        usage = msg.usage
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason == "refusal":
            text = ""
        return Completion(
            text=text,
            input_tokens=usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0),
            output_tokens=usage.output_tokens,  # includes thinking
            stop_reason=msg.stop_reason,
        )

    return complete


def _openai_complete(client, mid: str) -> Complete:
    extra: dict = {}
    reasoning = os.environ.get("ASFB_OPENAI_REASONING_EFFORT")
    if reasoning:
        extra["reasoning"] = {"effort": reasoning}

    def complete(system: str, user: str) -> Completion:
        r = client.responses.create(model=mid, instructions=system, input=user, **extra)
        return Completion(
            text=r.output_text,
            input_tokens=r.usage.input_tokens,
            output_tokens=r.usage.output_tokens,  # includes reasoning
        )

    return complete


def _google_complete(client, types, mid: str) -> Complete:
    def complete(system: str, user: str) -> Completion:
        r = client.models.generate_content(
            model=mid,
            contents=user,
            config=types.GenerateContentConfig(system_instruction=system),
        )
        um = r.usage_metadata
        return Completion(
            text=r.text or "",
            input_tokens=um.prompt_token_count or 0,
            output_tokens=(um.candidates_token_count or 0) + (um.thoughts_token_count or 0),
        )

    return complete
