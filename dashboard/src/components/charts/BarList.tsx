"use client";

import { useState } from "react";

import { Tooltip, useWidth, type TooltipState } from "./ChartCard";

export interface BarDatum {
  label: string;
  value: number;
  /** The value as it should read at the bar's tip, e.g. "€7,260.00". */
  display: string;
  detail?: string;
}

/**
 * Ranked horizontal bars: one hue, largest first, the value at each tip.
 *
 * The right form for "which vendor do we spend most with": bars on a shared
 * baseline compare more exactly than slices. Colour carries nothing here but
 * "this is the data", so every bar is the same blue.
 */
export default function BarList({ data, label }: { data: BarDatum[]; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<TooltipState | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(...data.map((d) => d.value), 0);

  return (
    <div ref={ref} className="relative" role="list" aria-label={label}>
      {data.map((datum, index) => {
        const share = max > 0 ? (datum.value / max) * 100 : 0;
        const show = (x: number, y: number) => {
          setActive(index);
          setTip({ x, y, value: datum.display, label: datum.label, detail: datum.detail });
        };
        return (
          <div
            key={datum.label}
            role="listitem"
            tabIndex={0}
            aria-label={`${datum.label}: ${datum.display}${datum.detail ? `, ${datum.detail}` : ""}`}
            className="grid grid-cols-1 items-center gap-x-3 gap-y-1 rounded py-1.5 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)]"
            onPointerMove={(event) => {
              const box = event.currentTarget.parentElement?.getBoundingClientRect();
              if (box) show(event.clientX - box.left, event.clientY - box.top);
            }}
            onPointerLeave={() => {
              setActive(null);
              setTip(null);
            }}
            onFocus={(event) => show(180, event.currentTarget.offsetTop)}
            onBlur={() => {
              setActive(null);
              setTip(null);
            }}
          >
            <span className="truncate text-sm text-ink-secondary" title={datum.label}>
              {datum.label}
            </span>
            <span className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="h-3 rounded-r transition-[filter]"
                style={{
                  // The bar leaves room for its value, which sits at the tip.
                  width: `calc((100% - 7rem) * ${share / 100})`,
                  minWidth: datum.value > 0 ? 2 : 0,
                  background: "var(--series-1)",
                  filter: active === index ? "brightness(1.25)" : undefined,
                }}
              />
              <span className="whitespace-nowrap text-sm tabular-nums text-ink">{datum.display}</span>
            </span>
          </div>
        );
      })}
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
