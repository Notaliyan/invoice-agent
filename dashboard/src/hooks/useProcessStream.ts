"use client";

/**
 * useProcessStream
 * ----------------
 * Uploads a document to `POST /process/stream` and follows the agent as it
 * works, through the endpoint's Server-Sent Events.
 *
 * `EventSource` cannot POST a file, so this reads the response body with a
 * stream reader and parses the SSE wire format by hand: the same approach as
 * ModulSearch's `useStreamingQuery`, including normalising CRLF to LF before
 * splitting events.
 *
 *   event: started   data: {filename, pages, chars}
 *   event: progress  data: {type: "step" | "tool", ...}
 *   event: result    data: the same body /process returns
 *   event: error     data: {error, detail}
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  API_URL,
  errorDetail,
  type ProcessResult,
  type ProgressEvent,
  type StartedEvent,
} from "@/lib/api";

export type Phase = "idle" | "running" | "done" | "error";

export interface StreamState {
  phase: Phase;
  filename: string | null;
  started: StartedEvent | null;
  events: ProgressEvent[];
  result: ProcessResult | null;
  error: string | null;
  /** Milliseconds since the upload began; ticks while running. */
  elapsedMs: number;
}

const INITIAL: StreamState = {
  phase: "idle",
  filename: null,
  started: null,
  events: [],
  result: null,
  error: null,
  elapsedMs: 0,
};

interface ParsedEvent {
  event: string;
  data: string;
}

/** Split a buffer into complete SSE events, returning what is left over. */
function takeEvents(buffer: string): { events: ParsedEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: ParsedEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length > 0) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

export function useProcessStream() {
  const [state, setState] = useState<StreamState>(INITIAL);
  const controller = useRef<AbortController | null>(null);
  const startedAt = useRef(0);

  // A running clock, so the page shows time passing between events.
  useEffect(() => {
    if (state.phase !== "running") return;
    const timer = setInterval(() => {
      setState((s) => ({ ...s, elapsedMs: performance.now() - startedAt.current }));
    }, 100);
    return () => clearInterval(timer);
  }, [state.phase]);

  // Stop reading if the page goes away mid-run.
  useEffect(() => () => controller.current?.abort(), []);

  const fail = useCallback((message: string) => {
    setState((s) => ({ ...s, phase: "error", error: message }));
  }, []);

  const start = useCallback(
    async (file: File) => {
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      startedAt.current = performance.now();
      setState({ ...INITIAL, phase: "running", filename: file.name });

      const body = new FormData();
      body.append("file", file);

      try {
        const response = await fetch(`${API_URL}/process/stream`, {
          method: "POST",
          body,
          signal: abort.signal,
        });
        if (!response.ok || !response.body) {
          // A bad upload is refused before any stream starts.
          fail(await errorDetail(response));
          return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finished = false;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          const { events, rest } = takeEvents(buffer);
          buffer = rest;

          for (const { event, data } of events) {
            const payload: unknown = JSON.parse(data);
            if (event === "started") {
              setState((s) => ({ ...s, started: payload as StartedEvent }));
            } else if (event === "progress") {
              setState((s) => ({ ...s, events: [...s.events, payload as ProgressEvent] }));
            } else if (event === "result") {
              finished = true;
              setState((s) => ({
                ...s,
                phase: "done",
                result: payload as ProcessResult,
                elapsedMs: performance.now() - startedAt.current,
              }));
            } else if (event === "error") {
              finished = true;
              const detail = (payload as { detail?: string }).detail;
              fail(detail ?? "The agent could not extract this document.");
            }
          }
        }

        if (!finished) fail("The connection closed before the agent finished.");
      } catch (error) {
        if (abort.signal.aborted) return;
        fail(
          error instanceof TypeError
            ? `Could not reach the API at ${API_URL}. Is it running?`
            : String(error),
        );
      }
    },
    [fail],
  );

  const reset = useCallback(() => {
    controller.current?.abort();
    setState(INITIAL);
  }, []);

  return { state, start, reset };
}
