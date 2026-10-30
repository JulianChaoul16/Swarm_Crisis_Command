"""Model access for specialist agents and report extraction.

`model_client_from_env` returns a Meta Responses API client when META_API_KEY, META_MODEL and
META_BASE_URL are set (environment first, then .env at the repo root, then
crisis_command/agents/.env). Otherwise it returns an unavailable client, so callers record an
explicit `unavailable` outcome instead of fabricated output. The key is never logged.
"""

import json
import os
from pathlib import Path
import socket
from typing import Any, Protocol
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILES = (REPO_ROOT / ".env", Path(__file__).resolve().parent / ".env")
DEFAULT_REASONING_EFFORT = "low"
MAX_OUTPUT_TOKENS = 8000


class ModelUnavailableError(RuntimeError):
    pass


class ModelClient(Protocol):
    model_id: str

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        """Return a JSON object matching `schema`, or raise on failure/timeout."""
        ...


class UnavailableModelClient:
    def __init__(self, reason: str) -> None:
        self.model_id = "unavailable"
        self.reason = reason

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        raise ModelUnavailableError(self.reason)


class MetaResponsesClient:
    """Structured calls to a Responses-style endpoint with a strict JSON schema.

    Requests set store=false. Any HTTP error, truncated response or non-JSON output raises,
    so callers surface the failure rather than use partial output.
    """

    def __init__(self, api_key: str, model: str, url: str,
                 reasoning_effort: str = DEFAULT_REASONING_EFFORT) -> None:
        self._api_key = api_key
        self.model_id = model
        self.url = url
        self.reasoning_effort = reasoning_effort

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        body = {
            "model": self.model_id,
            "instructions": system,
            "input": user,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": "output", "schema": schema,
                                "strict": True}},
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
        except (socket.timeout, TimeoutError) as error:
            raise TimeoutError(f"Model call exceeded {timeout:.0f}s.") from error
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Model API returned HTTP {error.code}.") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                raise TimeoutError(f"Model call exceeded {timeout:.0f}s.") from error
            raise RuntimeError(f"Model API unreachable: {error.reason}") from error

        if data.get("status") != "completed":
            raise RuntimeError(
                f"Model response {data.get('status')}: {data.get('incomplete_details')}")
        text = "".join(
            part.get("text", "")
            for item in data.get("output", []) if item.get("type") == "message"
            for part in item.get("content") or [] if part.get("type") == "output_text"
        )
        try:
            output = json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError("Model output was not valid JSON.") from error
        if not isinstance(output, dict):
            raise RuntimeError("Model output was not a JSON object.")
        return output


def _env_file_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in reversed(ENV_FILES):  # repo-root .env wins over the agents/.env fallback
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    name, value = line.split("=", 1)
                    values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def model_client_from_env() -> ModelClient:
    files = _env_file_values()

    def setting(name: str) -> str:
        return os.environ.get(name) or files.get(name, "")

    api_key, model, url = setting("META_API_KEY"), setting("META_MODEL"), setting("META_BASE_URL")
    if not (api_key and model and url):
        return UnavailableModelClient(
            "No model configured: set META_API_KEY, META_MODEL and META_BASE_URL "
            "(see .env.example)."
        )
    return MetaResponsesClient(
        api_key, model, url, setting("META_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT
    )
