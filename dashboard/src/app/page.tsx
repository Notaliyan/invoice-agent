"use client";

import Dropzone from "@/components/upload/Dropzone";
import ExtractionCard, { ExtractionSkeleton } from "@/components/upload/ExtractionCard";
import Timeline from "@/components/upload/Timeline";
import { useProcessStream } from "@/hooks/useProcessStream";

export default function UploadPage() {
  const { state, start, reset } = useProcessStream();
  const running = state.phase === "running";

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Extract a document</h1>
      <p className="mt-1 max-w-2xl text-sm text-ink-secondary">
        Upload an invoice. A tool-calling agent reads it, checks its own arithmetic in Python, and
        returns structured fields. You see each step as it happens.
      </p>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="space-y-6">
          <Dropzone onFile={start} disabled={running} filename={state.filename} />
          <Timeline state={state} />
        </div>

        <div className="space-y-4">
          {state.phase === "error" && (
            <div role="alert" className="rise rounded-xl border border-critical/40 bg-critical/10 p-5">
              <h2 className="text-sm font-semibold text-ink">
                <span aria-hidden="true" className="mr-1.5 font-bold text-critical">
                  ✕
                </span>
                This document could not be extracted
              </h2>
              <p className="mt-1 break-words text-sm text-ink-secondary">{state.error}</p>
              <button
                type="button"
                onClick={reset}
                className="mt-3 rounded-md border border-axis px-3 py-1.5 text-sm text-ink hover:bg-raised"
              >
                Try another document
              </button>
            </div>
          )}

          {state.result ? (
            <ExtractionCard result={state.result} />
          ) : (
            state.phase !== "error" && <ExtractionSkeleton running={running} />
          )}
        </div>
      </div>
    </div>
  );
}
