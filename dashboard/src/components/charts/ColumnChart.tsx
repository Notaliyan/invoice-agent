"use client";

import { useState } from "react";

import { niceTicks } from "@/lib/format";

import { Tooltip, useWidth, type TooltipState } from "./ChartCard";

export interface ColumnDatum {
  /** Short axis label, e.g. "Mar". */
  label: string;
  /** Full name for the tooltip, e.g. "March 2024". */
  name: string;
  value: number;
  display: string;
  detail?: string;
}

interface ColumnChartProps {
  data: ColumnDatum[];
  label: string;
  formatTick: (value: number) => string;
  height?: number;
}

const MARGIN = { top: 18, right: 8, bottom: 26, left: 44 };
const MAX_COLUMN = 24;

/** A column with a rounded cap and a square foot on the baseline. */
function columnPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

/**
 * Columns from a zero baseline, one hue. Used for values per time bucket.
 * The tallest column is labelled; the axis and the hover readout carry the rest.
 */
export default function ColumnChart({ data, label, formatTick, height = 220 }: ColumnChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<TooltipState | null>(null);
  const [active, setActive] = useState<number | null>(null);

  const max = Math.max(...data.map((d) => d.value), 0);
  const ticks = niceTicks(0, max || 1);
  const top = ticks[ticks.length - 1] ?? 1;

  const plotW = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotH = height - MARGIN.top - MARGIN.bottom;
  const band = data.length > 0 ? plotW / data.length : 0;
  const columnW = Math.max(2, Math.min(MAX_COLUMN, band - 2)); // at least a 2px gap
  const y = (value: number) => MARGIN.top + plotH - (value / top) * plotH;
  const tallest = data.reduce((best, d, i) => (d.value > (data[best]?.value ?? -1) ? i : best), 0);
  // Skip axis labels that would collide; the hover readout still names every column.
  const every = band > 0 ? Math.ceil(34 / band) : 1;

  const clear = () => {
    setActive(null);
    setTip(null);
  };

  return (
    <div ref={ref} className="relative">
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={y(tick)}
                y2={y(tick)}
                stroke={tick === 0 ? "var(--axis)" : "var(--hairline)"}
                strokeWidth={1}
              />
              <text
                x={MARGIN.left - 8}
                y={y(tick)}
                dy="0.32em"
                textAnchor="end"
                className="fill-ink-muted text-[11px] tabular-nums"
              >
                {formatTick(tick)}
              </text>
            </g>
          ))}

          {data.map((datum, index) => {
            const cx = MARGIN.left + band * index + band / 2;
            const columnH = (datum.value / top) * plotH;
            const show = () => {
              setActive(index);
              setTip({
                x: cx,
                y: y(datum.value),
                value: datum.display,
                label: datum.name,
                detail: datum.detail,
              });
            };
            return (
              <g key={datum.name}>
                {datum.value > 0 && (
                  <path
                    d={columnPath(cx - columnW / 2, y(datum.value), columnW, columnH)}
                    fill="var(--series-1)"
                    style={{ filter: active === index ? "brightness(1.25)" : undefined }}
                  />
                )}
                {index === tallest && datum.value > 0 && (
                  <text
                    x={cx}
                    y={y(datum.value) - 6}
                    textAnchor="middle"
                    className="fill-ink text-[11px] font-medium tabular-nums"
                  >
                    {datum.display}
                  </text>
                )}
                {index % every === 0 && (
                  <text
                    x={cx}
                    y={height - 8}
                    textAnchor="middle"
                    className="fill-ink-muted text-[11px]"
                  >
                    {datum.label}
                  </text>
                )}
                {/* The whole band is the hit target, not just the painted column. */}
                <rect
                  x={MARGIN.left + band * index}
                  y={MARGIN.top}
                  width={band}
                  height={plotH}
                  fill="transparent"
                  tabIndex={0}
                  role="img"
                  aria-label={`${datum.name}: ${datum.display}${datum.detail ? `, ${datum.detail}` : ""}`}
                  onPointerMove={show}
                  onPointerLeave={clear}
                  onFocus={show}
                  onBlur={clear}
                />
              </g>
            );
          })}
        </svg>
      )}
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
