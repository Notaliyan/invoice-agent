"""The agent loop.

A plain while-loop over OpenAI chat completions with tool calling. No framework:
the loop is ~60 lines and being able to read it is the point. Every tool call is
recorded into an `AgentTrace`, and every model call and tool call is mirrored to
Langfuse (see app/observability.py) when keys are configured.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from app.agent.prompts import (
    FINAL_STEP_PROMPT,
    NUDGE_PROMPT,
    PROMPT_FINGERPRINT,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.agent.tools import TERMINAL_TOOL, TOOL_FUNCTIONS, TOOL_SPECS, ToolContext
from app.config import Settings, get_settings
from app.observability import get_langfuse, observation, propagate
from app.pdf import PdfDocument
from app.schemas import AgentTrace, ExtractionResult, ToolCallRecord, ValidationReport

logger = logging.getLogger(__name__)

# Called with a small dict each time the agent starts a model step or finishes
# a tool call, so a caller can show progress while the run is still going.
ProgressCallback = Callable[[dict[str, Any]], None]


def _emit(on_event: ProgressCallback | None, event: dict[str, Any]) -> None:
    """Progress is a courtesy: a broken listener must not break the run."""
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logger.exception("progress callback failed")

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


def _tool_call_for_history(tc: Any) -> dict[str, Any]:
    """Echo a tool call back into the conversation.

    Provider-specific extras must survive the round trip: Gemini attaches a
    thought signature under ``extra_content`` and rejects the next request
    with a 400 if it is missing.
    """
    call: dict[str, Any] = {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }
    extra = getattr(tc, "extra_content", None)
    if extra:
        call["extra_content"] = extra
    return call


def _message_for_trace(message: Any) -> dict[str, Any]:
    """The assistant turn as plain data, for the generation's output."""
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (message.tool_calls or [])
        ],
    }


def _trace_metadata(trace: AgentTrace | None) -> dict[str, Any]:
    if trace is None:
        return {}
    return {
        "steps": trace.steps,
        "stop_reason": trace.stop_reason,
        "tool_sequence": [c.tool for c in trace.tool_calls],
        "failed_tool_calls": sum(1 for c in trace.tool_calls if not c.ok),
    }


class ExtractionAgent:
    def __init__(
        self,
        client: OpenAI | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.langfuse = get_langfuse(self.settings)
        if client is not None:
            self.client = client
        else:
            if not self.settings.openai_api_key:
                raise AgentError("OPENAI_API_KEY is not set.")
            self.client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_base_url or None,
                max_retries=self.settings.openai_max_retries,
            )

    def run(self, doc: PdfDocument, on_event: ProgressCallback | None = None) -> AgentResult:
        """Run the agent on one document, as a single Langfuse trace.

        `on_event` receives {"type": "step", ...} before each model call and
        {"type": "tool", ...} after each tool call."""
        model = self.settings.openai_model
        with (
            propagate(
                self.langfuse,
                version=PROMPT_VERSION,
                tags=[f"prompt:{PROMPT_VERSION}", f"model:{model}"],
                metadata={
                    "prompt_version": PROMPT_VERSION,
                    "prompt_fingerprint": PROMPT_FINGERPRINT,
                    "model": model,
                },
            ),
            observation(
                self.langfuse,
                name="invoice-extraction",
                as_type="agent",
                input={
                    "filename": doc.filename,
                    "pages": doc.page_count,
                    "chars": doc.char_count,
                },
            ) as root,
        ):
            try:
                result = self._loop(doc, trace_id=root.trace_id, on_event=on_event)
            except AgentError as exc:
                root.update(
                    level="ERROR",
                    status_message=str(exc),
                    metadata=_trace_metadata(exc.trace),
                )
                root.score_trace(name="extraction_succeeded", value=0, data_type="BOOLEAN")
                raise

            # Scores that need no ground truth, so production traffic is scored
            # too. Accuracy against labels comes from the eval run (evals/).
            root.update(
                output=result.extraction.model_dump(mode="json"),
                metadata=_trace_metadata(result.trace),
            )
            root.score_trace(name="extraction_succeeded", value=1, data_type="BOOLEAN")
            root.score_trace(
                name="validation_passed",
                value=1 if result.validation.passed else 0,
                data_type="BOOLEAN",
                comment="; ".join(
                    f"{c.name}: {c.detail}" for c in result.validation.checks if not c.passed
                )
                or None,
            )
            root.score_trace(name="model_confidence", value=result.extraction.confidence)
            return result

    def _loop(
        self,
        doc: PdfDocument,
        trace_id: str | None = None,
        on_event: ProgressCallback | None = None,
    ) -> AgentResult:
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
            _emit(on_event, {"type": "step", "step": step, "max_steps": max_steps})

            with observation(
                self.langfuse,
                name=f"llm-step-{step}",
                as_type="generation",
                model=self.settings.openai_model,
                model_parameters={"temperature": self.settings.agent_temperature},
                input=messages,
                metadata={"step": step, "final_step": step == max_steps},
            ) as generation:
                response = self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    tools=TOOL_SPECS,
                    tool_choice="auto",
                    temperature=self.settings.agent_temperature,
                )

                usage = getattr(response, "usage", None)
                step_prompt = (usage.prompt_tokens or 0) if usage else 0
                step_completion = (usage.completion_tokens or 0) if usage else 0
                prompt_tokens += step_prompt
                completion_tokens += step_completion

                message = response.choices[0].message
                generation.update(
                    output=_message_for_trace(message),
                    usage_details={"input": step_prompt, "output": step_completion},
                )

            if not message.tool_calls:
                logger.debug("step %s: model returned prose, nudging", step)
                messages.append({"role": "assistant", "content": message.content or ""})
                messages.append({"role": "user", "content": NUDGE_PROMPT})
                continue

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [_tool_call_for_history(tc) for tc in message.tool_calls],
                }
            )

            for tc in message.tool_calls:
                record, payload = self._execute(ctx, step, tc)
                tool_calls.append(record)
                _emit(
                    on_event,
                    {
                        "type": "tool",
                        "step": step,
                        "tool": record.tool,
                        "ok": record.ok,
                        "summary": record.result_summary,
                        "duration_ms": record.duration_ms,
                    },
                )
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
            prompt_version=PROMPT_VERSION,
            langfuse_trace_id=trace_id,
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

        with observation(
            self.langfuse,
            name=name,
            as_type="tool",
            input=tool_call.function.arguments,
            metadata={"step": step},
        ) as span:
            arguments, payload = self._call_tool(ctx, name, tool_call.function.arguments)
            record = self._record(step, name, arguments, payload, started)
            span.update(
                input=arguments or tool_call.function.arguments,
                output=_serialise(payload),
                level="DEFAULT" if record.ok else "WARNING",
                status_message=None if record.ok else record.result_summary,
            )
        return record, payload

    @staticmethod
    def _call_tool(
        ctx: ToolContext, name: str, raw_arguments: str | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run one tool. Every failure comes back as an error payload."""
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return {}, {"error": f"arguments were not valid JSON: {exc}"}

        fn = TOOL_FUNCTIONS.get(name)
        if fn is None:
            return arguments, {
                "error": f"unknown tool {name!r}; available: {sorted(TOOL_FUNCTIONS)}"
            }

        ctx.calls.append(name)
        try:
            payload = fn(ctx, **arguments)
        except TypeError as exc:
            payload = {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # a tool crash must not kill the run
            logger.exception("tool %s raised", name)
            payload = {"error": f"{name} failed: {type(exc).__name__}: {exc}"}
        return arguments, payload

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
