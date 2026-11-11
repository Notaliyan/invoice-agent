"""Langfuse tracing, kept behind one thin seam.

The agent loop calls `observation(...)` and `propagate(...)` and never imports
Langfuse itself. With no keys configured both hand back inert stand-ins, so the
loop has a single code path and the offline tests need no network.

Trace shape for one document:

    invoice-extraction            (agent)       input: document stats, output: extraction
      llm-step-1                  (generation)  messages in, tool calls out, token usage
      extract_invoice_fields      (tool)        arguments in, result out
      llm-step-2                  (generation)
      validate_amounts            (tool)
      ...
"""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

# One client per credential set. Langfuse batches and exports on a background
# thread, so the client has to outlive individual requests.
_clients: dict[tuple[str, str, str, str], Any] = {}


class NoopObservation:
    """Stands in for a Langfuse observation when tracing is off."""

    trace_id: str | None = None

    def update(self, **_: Any) -> NoopObservation:
        return self

    def score_trace(self, **_: Any) -> None:
        return None


_NOOP = NoopObservation()


def get_langfuse(settings: Settings) -> Any | None:
    """The Langfuse client for these settings, or None when tracing is off."""
    if not settings.langfuse_enabled:
        return None

    key = (
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        settings.langfuse_base_url,
        settings.langfuse_environment,
    )
    if key not in _clients:
        from langfuse import Langfuse  # lazy: pulls in OpenTelemetry

        _clients[key] = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_environment,
        )
    return _clients[key]


def observation(client: Any | None, **kwargs: Any) -> AbstractContextManager[Any]:
    """Open a span / generation / tool observation as the current one.

    `kwargs` are passed straight to `Langfuse.start_as_current_observation`
    (name, as_type, input, metadata, model, ...).
    """
    if client is None:
        return nullcontext(_NOOP)
    return client.start_as_current_observation(**kwargs)


def propagate(client: Any | None, **kwargs: Any) -> AbstractContextManager[Any]:
    """Stamp trace-level attributes (version, tags, metadata) on everything
    opened inside the block."""
    if client is None:
        return nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(**kwargs)


def flush(settings: Settings) -> None:
    """Push buffered events out. Call before a short-lived process exits."""
    client = get_langfuse(settings)
    if client is not None:
        client.flush()
