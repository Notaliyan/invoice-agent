"""The agent loop.

A plain while-loop over OpenAI chat completions with tool calling. No framework:
the loop is ~60 lines and being able to read it is the point. Every tool call is
recorded into an `AgentTrace`, which is what Phase 3 will ship to Langfuse.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from openai import OpenAI

from app.agent.prompts import (
    FINAL_STEP_PROMPT,
    NUDGE_PROMPT,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.agent.tools import TERMINAL_TOOL, TOOL_FUNCTIONS, TOOL_SPECS, ToolContext
from app.config import Settings, get_settings
from app.pdf import PdfDocument
from app.schemas import AgentTrace, ExtractionResult, ToolCallRecord, ValidationReport

logger = logging.getLogger(__name__)

# Tool results are fed back into the prompt, so cap them.
MAX_TOOL_RESULT_CHARS = 8000


class AgentError(Exception):
    """The agent could not produce a validated result."""

    def __init__(self, message: str, trace: AgentTrace | None = None) -> None:
        super().__init__(message)
        self.trace = trace


class AgentResult:
    def __init__(
        self,
        extraction: ExtractionResult,
        validation: ValidationReport,
        trace: AgentTrace,
    ) -> None:
        self.extraction = extraction
        self.validation = validation
        self.trace = trace


def _serialise(result: Any) -> str:
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + " ... [result truncated]"
    return text


def _summarise(name: str, result: dict[str, Any]) -> str:
    """A short human-readable line for the trace, so it stays skimmable."""
    if "error" in result:
        return f"error: {result['error']}"
    if name == "search_document":
        return f"{result.get('match_count', 0)} matches for {result.get('query')!r}"
    if name == "get_page_text":
        return f"page {result.get('page')}, {len(result.get('text', ''))} chars"
    if name == "extract_invoice_fields":
        counts = {
            k: (len(v) if isinstance(v, (list, dict)) else 1)
            for k, v in result.items()
            if k != "scanned_lines"
        }
        return f"harvested {counts} from {result.get('scanned_lines')} lines"
    if name == "classify_document_type":
        return (
            f"proposed={result.get('proposed_type')} "
            f"heuristic={result.get('heuristic_type')} agrees={result.get('agrees')}"
        )
    if name == "validate_amounts":
        failed = [c["name"] for c in result.get("checks", []) if not c["passed"]]
        return f"passed={result.get('passed')}" + (f", failed: {failed}" if failed else "")
    if name == TERMINAL_TOOL:
        return "accepted" if result.get("accepted") else f"rejected: {result.get('error')}"
    return "ok"


class ExtractionAgent:
    def __init__(
        self,
        client: OpenAI | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if client is not None:
            self.client = client
        else:
            if not self.settings.openai_api_key:
                raise AgentError("OPENAI_API_KEY is not set.")
            self.client = OpenAI(api_key=self.settings.openai_api_key)

    def run(self, doc: PdfDocument) -> AgentResult:
        ctx = ToolContext(doc=doc)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(
                    filename=doc.filename,
                    page_count=doc.page_count,
                    char_count=doc.char_count,
                    preview=doc.preview(self.settings.page_preview_chars),
                ),
            },
        ]

        tool_calls: list[ToolCallRecord] = []
        prompt_tokens = completion_tokens = 0
        stop_reason = "step_budget_exhausted"
        max_steps = self.settings.agent_max_steps
        step = 0

        for step in range(1, max_steps + 1):
            if step == max_steps:
                messages.append({"role": "user", "content": FINAL_STEP_PROMPT})

            response = self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                tools=TOOL_SPECS,
                tool_choice="auto",
                temperature=self.settings.agent_temperature,
            )

            usage = getattr(response, "usage", None)
            if usage:
                prompt_tokens += usage.prompt_tokens or 0
                completion_tokens += usage.completion_tokens or 0

            message = response.choices[0].message

            if not message.tool_calls:
                logger.debug("step %s: model returned prose, nudging", step)
                messages.append({"role": "assistant", "content": message.content or ""})
                messages.append({"role": "user", "content": NUDGE_PROMPT})
                continue

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }
            )

            for tc in message.tool_calls:
                record, payload = self._execute(ctx, step, tc)
                tool_calls.append(record)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _serialise(payload),
                    }
                )

            if ctx.finished:
                stop_reason = "submitted"
                break

        trace = AgentTrace(
            model=self.settings.openai_model,
            steps=step,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        if ctx.result is None:
            raise AgentError(
                f"Agent did not produce a validated extraction within {max_steps} steps.",
                trace=trace,
            )

        return AgentResult(
            extraction=ctx.result,
            validation=ctx.validation or ValidationReport(passed=False, checks=[]),
            trace=trace,
        )

    def _execute(
        self, ctx: ToolContext, step: int, tool_call: Any
    ) -> tuple[ToolCallRecord, dict[str, Any]]:
        name = tool_call.function.name
        started = time.perf_counter()

        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            payload = {"error": f"arguments were not valid JSON: {exc}"}
            return self._record(step, name, {}, payload, started), payload

        fn = TOOL_FUNCTIONS.get(name)
        if fn is None:
            payload = {
                "error": f"unknown tool {name!r}; available: {sorted(TOOL_FUNCTIONS)}"
            }
            return self._record(step, name, arguments, payload, started), payload

        ctx.calls.append(name)
        try:
            payload = fn(ctx, **arguments)
        except TypeError as exc:
            payload = {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # a tool crash must not kill the run
            logger.exception("tool %s raised", name)
            payload = {"error": f"{name} failed: {type(exc).__name__}: {exc}"}

        return self._record(step, name, arguments, payload, started), payload

    @staticmethod
    def _record(
        step: int,
        name: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
        started: float,
    ) -> ToolCallRecord:
        return ToolCallRecord(
            step=step,
            tool=name,
            arguments=arguments,
            ok="error" not in payload and payload.get("accepted") is not False,
            result_summary=_summarise(name, payload),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
