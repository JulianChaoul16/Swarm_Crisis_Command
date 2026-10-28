"""Model access for specialist agents and report extraction.

No Meta model integration exists yet. `model_client_from_env` returns an unavailable
client so callers record an explicit `unavailable` outcome instead of fabricated output.
"""

import os
from typing import Any, Protocol


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


def model_client_from_env() -> ModelClient:
    if not os.environ.get("META_API_KEY"):
        return UnavailableModelClient(
            "No model configured: set META_API_KEY and META_MODEL (see .env.example)."
        )
    return UnavailableModelClient(
        "META_API_KEY is set, but the Meta model client is not implemented yet."
    )
