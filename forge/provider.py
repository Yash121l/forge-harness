"""Small OpenAI-compatible chat completions client using only the standard library."""
from __future__ import annotations

import datetime as _datetime
import email.utils
import http.client
import ipaddress
import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_TOKEN_PARAMETER = "max_tokens"


class ProviderError(RuntimeError):
    """A safe, user-facing provider error that never includes credentials/body text."""


@dataclass(frozen=True)
class ModelConfig:
    api_key: str = field(repr=False)
    model: str
    base_url: str = DEFAULT_BASE_URL
    max_output_tokens: int = 4096
    timeout: float = 90.0
    token_parameter: str = DEFAULT_TOKEN_PARAMETER
    temperature: Optional[float] = None

    @classmethod
    def from_env(cls) -> "ModelConfig":
        key = os.environ.get("AI_API_KEY", "").strip()
        model = os.environ.get("AI_MODEL", DEFAULT_MODEL).strip()
        if not key:
            raise ProviderError("AI_API_KEY is required")
        if not model:
            raise ProviderError("AI_MODEL is required")
        try:
            max_tokens = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "4096"))
            timeout = float(os.environ.get("AI_TIMEOUT", "90"))
            temp_raw = os.environ.get("AI_TEMPERATURE")
            temperature = float(temp_raw) if temp_raw is not None else None
        except ValueError:
            raise ProviderError("AI_MAX_OUTPUT_TOKENS, AI_TIMEOUT, and AI_TEMPERATURE must be numeric") from None
        token_parameter = os.environ.get("AI_TOKEN_PARAMETER", DEFAULT_TOKEN_PARAMETER).strip()
        if token_parameter not in ("max_completion_tokens", "max_tokens"):
            raise ProviderError("AI_TOKEN_PARAMETER must be max_completion_tokens or max_tokens")
        return cls(key, model, os.environ.get("AI_BASE_URL", DEFAULT_BASE_URL).strip(),
                   max_tokens, timeout, token_parameter, temperature)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ProviderError("AI_API_KEY is required")
        if not self.model:
            raise ProviderError("AI_MODEL is required")
        if isinstance(self.max_output_tokens, bool) or not isinstance(self.max_output_tokens, int) or self.max_output_tokens <= 0:
            raise ProviderError("AI_MAX_OUTPUT_TOKENS must be a positive integer")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ProviderError("AI_TIMEOUT must be positive")
        if self.temperature is not None and (isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)) or not math.isfinite(self.temperature)):
            raise ProviderError("AI_TEMPERATURE must be a finite number")
        if self.token_parameter not in ("max_completion_tokens", "max_tokens"):
            raise ProviderError("AI_TOKEN_PARAMETER must be max_completion_tokens or max_tokens")
        _validate_base_url(self.base_url)


def _validate_base_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        if not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError
        local = parsed.hostname.lower() == "localhost"
        try:
            local = local or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ProviderError("AI_BASE_URL must use HTTPS, except for loopback HTTP endpoints")
        if parsed.scheme not in ("http", "https"):
            raise ProviderError("AI_BASE_URL must use HTTPS, except for loopback HTTP endpoints")
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("AI_BASE_URL is invalid") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Never follow redirects: even same-host redirects can change the endpoint.
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class Provider:
    def __init__(self, config: Optional[ModelConfig] = None) -> None:
        self.config = config or ModelConfig.from_env()
        self._opener = urllib.request.build_opener(_NoRedirect())

    def complete(self, messages: Sequence[Mapping[str, Any]], tools: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            self.config.token_parameter: self.config.max_output_tokens,
        }
        if tools:
            payload["tools"] = list(tools)
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Authorization": "Bearer " + self.config.api_key,
                     "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with self._opener.open(request, timeout=self.config.timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ProviderError("AI provider response exceeded the 8 MiB limit")
                return _normalize_response(raw)
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                # Do not read or include provider error bodies; they may contain sensitive data.
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(_retry_delay(retry_after, attempt))
                    continue
                if status in (401, 403):
                    raise ProviderError("AI provider authentication failed; check AI_API_KEY") from None
                if 300 <= status < 400:
                    raise ProviderError("AI provider returned a redirect; redirects are not followed") from None
                raise ProviderError("AI provider request failed (HTTP {})".format(status)) from None
            except (socket.timeout, TimeoutError):
                raise ProviderError("AI provider request timed out") from None
            except urllib.error.URLError as exc:
                if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                    raise ProviderError("AI provider request timed out") from None
                raise ProviderError("Could not connect to AI provider") from None
            except http.client.HTTPException:
                raise ProviderError("AI provider returned an invalid HTTP response") from None
            except OSError:
                raise ProviderError("Could not connect to AI provider") from None
        raise ProviderError("AI provider request failed")


def _retry_delay(value: Optional[str], attempt: int = 0) -> float:
    if not value:
        return min(5.0, 0.5 * (2 ** attempt))
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            when = email.utils.parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=_datetime.timezone.utc)
            delay = (when - _datetime.datetime.now(_datetime.timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0.5
    return min(5.0, max(0.0, delay))


def _normalize_response(raw: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
        choice = data["choices"][0]
        message = choice["message"]
        usage = data.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError
        normalized = {"role": "assistant", "content": content}
        calls = message.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list):
                raise ValueError
            seen_ids = set()
            clean_calls = []
            for call in calls:
                if not isinstance(call, dict) or call.get("type") != "function":
                    raise ValueError
                call_id = call.get("id")
                function = call.get("function")
                if not isinstance(call_id, str) or not call_id or call_id in seen_ids or not isinstance(function, dict):
                    raise ValueError
                seen_ids.add(call_id)
                name, arguments = function.get("name"), function.get("arguments")
                if not isinstance(name, str) or not name or not isinstance(arguments, str):
                    raise ValueError
                clean_calls.append({"id": call_id, "type": "function",
                                    "function": {"name": name, "arguments": arguments}})
                # Gemini 3 uses thought signatures attached to tool calls. Keep the
                # provider extension so it can be sent back in the next turn.
                if "extra_content" in call:
                    if not isinstance(call["extra_content"], dict):
                        raise ValueError
                    clean_calls[-1]["extra_content"] = call["extra_content"]
            normalized["tool_calls"] = clean_calls
        if choice.get("finish_reason") == "length" and not content and not normalized.get("tool_calls"):
            raise ValueError
        if (isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens < 0 or
                isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or output_tokens < 0):
            raise ValueError
        return {"message": normalized, "input_tokens": input_tokens, "output_tokens": output_tokens}
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        raise ProviderError("AI provider returned a malformed response") from None
