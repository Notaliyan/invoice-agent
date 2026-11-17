"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";

interface DropzoneProps {
  onFile: (file: File) => void;
  disabled: boolean;
  /** Shown instead of the prompt once a document has been chosen. */
  filename: string | null;
}

/** Drag a PDF in, or click (or press Enter) to pick one. */
export default function Dropzone({ onFile, disabled, filename }: DropzoneProps) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);

  const accept = useCallback(
    (file: File | undefined) => {
      if (!file) return;
      const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
      if (!isPdf) {
        setRejected(`${file.name} is not a PDF.`);
        return;
      }
      setRejected(null);
      onFile(file);
    },
    [onFile],
  );

  const onDrop = useCallback(
    (event: DragEvent<HTMLButtonElement>) => {
      event.preventDefault();
      setOver(false);
      if (!disabled) accept(event.dataTransfer.files[0]);
    },
    [accept, disabled],
  );

  return (
    <div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={`flex w-full flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          over
            ? "border-series bg-series/10"
            : "border-axis bg-surface hover:border-ink-muted hover:bg-raised/40"
        } disabled:cursor-not-allowed disabled:opacity-60`}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-9 w-9 text-ink-muted"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 16V4m0 0L8 8m4-4 4 4M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"
          />
        </svg>
        {filename ? (
          <span className="text-sm text-ink">
            <span className="font-medium">{filename}</span>
            {!disabled && <span className="text-ink-muted"> · drop another to replace it</span>}
          </span>
        ) : (
          <span className="text-sm text-ink">
            <span className="font-medium">Drop a PDF here</span>
            <span className="text-ink-muted"> or click to choose one</span>
          </span>
        )}
        <span className="text-xs text-ink-muted">Invoices, receipts and purchase orders</span>
      </button>

      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          accept(event.target.files?.[0]);
          event.target.value = ""; // let the same file be chosen again
        }}
      />

      {rejected && (
        <p role="alert" className="mt-2 text-sm text-critical">
          {rejected}
        </p>
      )}
    </div>
  );
}
