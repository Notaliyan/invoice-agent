import type { StreamState } from "@/hooks/useProcessStream";
import type { ProgressEvent } from "@/lib/api";
import { seconds } from "@/lib/format";

/** What each tool call means, in words a viewer of the demo can follow. */
const TOOL_LABELS: Record<string, string> = {
  get_page_text: "Read a page",
  search_document: "Searched the document",
  extract_invoice_fields: "Collected candidate amounts, dates and IDs",
  classify_document_type: "Checked the document type",
  validate_amounts: "Checked the arithmetic in Python",
  submit_extraction: "Submitted the result",
};

type ToolEvent = Extract<ProgressEvent, { type: "tool" }>;
type StepEvent = Extract<ProgressEvent, { type: "step" }>;

function Dot({ tone }: { tone: "done" | "failed" | "active" | "idle" }) {
  if (tone === "active") {
    return (
      <span className="flex h-5 w-5 items-center justify-center" aria-hidden="true">
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-series border-t-transparent" />
      </span>
    );
  }
  const styles = {
    done: "bg-good/15 text-good",
    failed: "bg-warning/15 text-warning",
    idle: "bg-raised text-ink-muted",
  }[tone];
  return (
    <span
      aria-hidden="true"
      className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${styles}`}
    >
      {tone === "done" ? "✓" : tone === "failed" ? "!" : "·"}
    </span>
  );
}

/** The agent's steps as they arrive: one row per tool call. */
export default function Timeline({ state }: { state: StreamState }) {
  const { phase, started, events, elapsedMs } = state;
  const tools = events.filter((event): event is ToolEvent => event.type === "tool");
  const lastStep = [...events]
    .reverse()
    .find((event): event is StepEvent => event.type === "step");
  const running = phase === "running";

  return (
    <section aria-label="Agent activity" className="rounded-xl border border-hairline bg-surface p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-semibold text-ink">Agent activity</h2>
        <p className="text-xs tabular-nums text-ink-muted" aria-live="off">
          {phase === "idle" ? "Waiting for a document" : seconds(elapsedMs)}
        </p>
      </div>

      {phase === "idle" ? (
        <p className="mt-4 text-sm text-ink-muted">
          Each tool the agent calls appears here the moment it finishes.
        </p>
      ) : (
        <ol className="mt-4 space-y-3" aria-live="polite">
          <li className="rise flex gap-3">
            <Dot tone={started ? "done" : "active"} />
            <div className="text-sm">
              <p className="text-ink">{started ? "Read the PDF" : "Uploading and reading the PDF"}</p>
              {started && (
                <p className="text-xs text-ink-muted">
                  {started.pages} {started.pages === 1 ? "page" : "pages"},{" "}
                  {started.chars.toLocaleString("en-GB")} characters
                </p>
              )}
            </div>
          </li>

          {tools.map((event, index) => (
            <li key={index} className="rise flex gap-3">
              <Dot tone={event.ok ? "done" : "failed"} />
              <div className="min-w-0 text-sm">
                <p className="text-ink">
                  {TOOL_LABELS[event.tool] ?? event.tool}
                  {!event.ok && <span className="text-warning"> · sent back to the agent</span>}
                </p>
                <p className="break-words text-xs text-ink-muted">
                  <span className="font-mono">{event.tool}</span> · {event.summary}
                </p>
              </div>
            </li>
          ))}

          {running && started && (
            <li className="flex gap-3">
              <Dot tone="active" />
              <p className="text-sm text-ink-secondary">
                The model is deciding what to do next
                {lastStep && (
                  <span className="text-ink-muted">
                    {" "}
                    · step {lastStep.step} of at most {lastStep.max_steps}
                  </span>
                )}
              </p>
            </li>
          )}
        </ol>
      )}
    </section>
  );
}
