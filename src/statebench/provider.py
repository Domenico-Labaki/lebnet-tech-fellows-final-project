from __future__ import annotations

import time
import json
import os
import math
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ollama import Client

from .models import ModelTurn


def _read_local_env(name: str) -> str | None:
    """Read one unquoted key from the ignored project-local `.env` file.

    Environment variables take precedence; this is only a bridge for desktop
    shells that do not share their process environment with the benchmark.
    """
    env_file = Path.cwd() / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'") or None
    return None


class OllamaProvider:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.client = Client(host=settings["ollama_host"])

    def doctor(self) -> dict[str, Any]:
        models = self.client.list()
        model_names = [item.model for item in models.models]
        running = self.client.ps()
        return {
            "available_models": model_names,
            "running_models": [item.model_dump() for item in running.models],
            "selected_model_present": self.settings["model"] in model_names,
        }

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        started = time.perf_counter()
        response = self.client.chat(
            model=self.settings["model"],
            messages=messages,
            tools=tools,
            think=self.settings["thinking"],
            options={
                "temperature": self.settings["temperature"],
                "top_p": self.settings["top_p"],
                "top_k": self.settings["top_k"],
                "seed": self.settings["seed"],
                "num_ctx": self.settings["num_ctx"],
                "num_predict": self.settings["num_predict"],
            },
            keep_alive="30m",
        )
        message = response.message
        calls = [call.model_dump() for call in (message.tool_calls or [])]
        raw = response.model_dump()
        return ModelTurn(
            content=message.content or "",
            tool_calls=calls,
            prompt_tokens=int(getattr(response, "prompt_eval_count", 0) or 0),
            completion_tokens=int(getattr(response, "eval_count", 0) or 0),
            latency_seconds=time.perf_counter() - started,
            raw=raw,
        )


class ProviderRateLimitError(RuntimeError):
    """A provider rejected a request because the current quota is exhausted."""

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderToolValidationError(RuntimeError):
    """The provider rejected a model-generated call to an unknown tool."""


class GroqProvider:
    """Minimal OpenAI-compatible Groq client using only the standard library.

    Keeping this client small avoids a second SDK and makes raw provider responses
    available in each trial record for reproducibility.
    """

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.api_key_env = str(settings.get("api_key_env", "GROQ_API_KEY"))
        self.api_key = os.environ.get(self.api_key_env) or _read_local_env(self.api_key_env)
        self.api_base = str(settings.get("api_base", "https://api.groq.com/openai/v1")).rstrip("/")
        if not self.api_key:
            raise RuntimeError(
                f"Groq API key is missing. Set {self.api_key_env} in the current shell; do not add it to a file."
            )

    def doctor(self) -> dict[str, Any]:
        return {
            "provider": "groq",
            "api_base": self.api_base,
            "selected_model": self.settings["model"],
            "api_key_env": self.api_key_env,
            "api_key_present": bool(self.api_key),
        }

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        payload: dict[str, Any] = {
            "model": self.settings["model"],
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self.settings["temperature"],
            "top_p": self.settings["top_p"],
            "max_completion_tokens": self.settings["num_predict"],
        }
        # Only send provider-specific reasoning controls when deliberately set
        # in the configuration; different Groq models support different knobs.
        if self.settings.get("reasoning_effort"):
            payload["reasoning_effort"] = self.settings["reasoning_effort"]
        request = Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            # Groq's edge can reject urllib's default bot-like User-Agent with
            # Cloudflare 1010. Identify this reproducible research client
            # explicitly, as production API clients and the Groq SDK do.
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "StateBench/0.1 (research benchmark; contact=local-user)",
            },
            method="POST",
        )
        started = time.perf_counter()
        retries = int(self.settings.get("max_rate_limit_retries", 8))
        padding = float(self.settings.get("rate_limit_padding_seconds", 2))
        for attempt in range(retries + 1):
            try:
                with urlopen(request, timeout=int(self.settings.get("request_timeout_seconds", 120))) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                if error.code != 429:
                    if error.code == 400 and "tool_use_failed" in body:
                        raise ProviderToolValidationError(body) from error
                    raise RuntimeError(f"Groq API error {error.code}: {body}") from error
                retry_after = error.headers.get("Retry-After")
                seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
                # A daily limit cannot be usefully waited out inside a command.
                # Short provider windows (RPM/TPM) can: retrying here preserves
                # the current tool conversation rather than restarting a trial.
                normalized = body.lower()
                if "daily" in normalized or "tpd" in normalized or attempt == retries:
                    raise ProviderRateLimitError(body, seconds) from error
                delay = min(60, max(1, math.ceil(seconds or 5) + padding))
                print(
                    f"Groq short-window rate limit; continuing this trial in {delay:g}s "
                    f"(retry {attempt + 1}/{retries})",
                    flush=True,
                )
                time.sleep(delay)
            except URLError as error:
                raise ConnectionError(f"Could not reach Groq: {error.reason}") from error
        else:  # pragma: no cover - defensive: the loop always breaks or raises.
            raise RuntimeError("Groq request retry loop exited unexpectedly")

        choice = raw["choices"][0]["message"]
        calls = choice.get("tool_calls") or []
        usage = raw.get("usage") or {}
        return ModelTurn(
            content=choice.get("content") or "",
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_seconds=time.perf_counter() - started,
            raw=raw,
        )


def create_provider(settings: dict[str, Any]) -> OllamaProvider | GroqProvider:
    provider = str(settings.get("provider", "ollama")).lower()
    if provider == "ollama":
        return OllamaProvider(settings)
    if provider == "groq":
        return GroqProvider(settings)
    raise ValueError(f"Unsupported provider: {provider}")
