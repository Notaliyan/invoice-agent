"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** Width of an element, kept current as the layout changes. */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, width];
}

export interface TooltipState {
  /** Pixels from the chart's left and top edges. */
  x: number;
  y: number;
  /** The value leads; the label follows. */
  value: string;
  label: string;
  detail?: string;
}

/** A hover / focus readout. It repeats what the table view holds; it gates nothing. */
export function Tooltip({ tip, width }: { tip: TooltipState | null; width: number }) {
  if (!tip) return null;
  // Flip to the left of the pointer near the right edge, so it is never clipped.
  const flip = tip.x > width - 170;
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-10 w-max max-w-[15rem] rounded-md border border-axis bg-raised px-2.5 py-1.5 shadow-lg"
      style={{
        left: flip ? undefined : tip.x + 12,
        right: flip ? width - tip.x + 12 : undefined,
        top: Math.max(0, tip.y - 12),
      }}
    >
      <p className="text-sm font-semibold text-ink">{tip.value}</p>
      <p className="text-xs text-ink-secondary">{tip.label}</p>
      {tip.detail && <p className="text-xs text-ink-muted">{tip.detail}</p>}
    </div>
  );
}

interface ChartCardProps {
  title: string;
  /** Says what is plotted, so a single-series chart needs no legend. */
  subtitle: string;
  children: ReactNode;
  /** The same data as a table: every value reachable without hovering. */
  table?: { columns: string[]; rows: (string | number)[][] };
  empty?: boolean;
  emptyMessage?: string;
}

export default function ChartCard({
  title,
  subtitle,
  children,
  table,
  empty = false,
  emptyMessage = "No data yet.",
}: ChartCardProps) {
  return (
    <section className="rounded-xl border border-hairline bg-surface p-5">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>

      <div className="mt-4">
        {empty ? <p className="py-8 text-center text-sm text-ink-muted">{emptyMessage}</p> : children}
      </div>

      {table && !empty && (
        <details className="mt-4 border-t border-hairline pt-3">
          <summary className="cursor-pointer text-xs text-ink-muted hover:text-ink-secondary">
            View as table
          </summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-ink-muted">
                  {table.columns.map((column, index) => (
                    <th
                      key={column}
                      scope="col"
                      className={`py-1 pr-4 font-normal ${index > 0 ? "text-right" : ""}`}
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {table.rows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="border-t border-hairline">
                    {row.map((cell, index) => (
                      <td
                        key={index}
                        className={`py-1 pr-4 ${index > 0 ? "text-right text-ink-secondary" : "text-ink"}`}
                      >
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
