"use client";

import { useState } from "react";

import { Tooltip, useWidth, type TooltipState } from "./ChartCard";

export interface LinePoint {
  /** Two-line axis label: e.g. ["16 Nov", "v2"]. */
  label: [string, string];
  name: string;
  value: number;
  display: string;
  detail?: string;
}

interface LineChartProps {
  data: LinePoint[];
  label: string;
  /** The y-axis ticks, low to high. The first and last are the domain. */
  ticks: number[];
  formatTick: (value: number) => string;
  height?: number;
}

const MARGIN = { top: 18, right: 52, bottom: 40, left: 48 };

/**
 * One series over an ordered sequence of runs: a 2px line with ringed markers.
 * The last point is labelled; a crosshair snaps to the nearest run on hover,
 * so the reader aims at a run, never at the line itself.
 */
export default function LineChart({ data, label, ticks, formatTick, height = 220 }: LineChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<TooltipState | null>(null);
  const [active, setActive] = useState<number | null>(null);

  const low = ticks[0] ?? 0;
  const high = ticks[ticks.length - 1] ?? 1;
  const plotW = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotH = height - MARGIN.top - MARGIN.bottom;
  const x = (index: number) =>
    MARGIN.left + (data.length > 1 ? (plotW * index) / (data.length - 1) : plotW / 2);
  const y = (value: number) => MARGIN.top + plotH - ((value - low) / (high - low || 1)) * plotH;

  const path = data.map((point, index) => `${index === 0 ? "M" : "L"}${x(index)},${y(point.value)}`).join(" ");
  const last = data.length - 1;

  const show = (index: number) => {
    const point = data[index];
    if (!point) return;
    setActive(index);
    setTip({ x: x(index), y: y(point.value), value: point.display, label: point.name, detail: point.detail });
  };
  const clear = () => {
    setActive(null);
    setTip(null);
  };
  const nearest = (clientX: number, left: number) => {
    if (data.length < 2) return 0;
    const ratio = (clientX - left - MARGIN.left) / (plotW || 1);
    return Math.max(0, Math.min(last, Math.round(ratio * last)));
  };

  return (
    <div ref={ref} className="relative">
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={label}
          onPointerMove={(event) => show(nearest(event.clientX, event.currentTarget.getBoundingClientRect().left))}
          onPointerLeave={clear}
        >
          {ticks.map((tick, index) => (
            <g key={tick}>
              <line
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={y(tick)}
                y2={y(tick)}
                stroke={index === 0 ? "var(--axis)" : "var(--hairline)"}
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

          {active !== null && (
            <line
              x1={x(active)}
              x2={x(active)}
              y1={MARGIN.top}
              y2={MARGIN.top + plotH}
              stroke="var(--axis)"
              strokeWidth={1}
            />
          )}

          {data.length > 1 && (
            <path
              d={path}
              fill="none"
              stroke="var(--series-1)"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )}

          {data.map((point, index) => (
            <g key={point.name}>
              <circle
                cx={x(index)}
                cy={y(point.value)}
                r={active === index ? 5.5 : 4.5}
                fill="var(--series-1)"
                stroke="var(--surface)"
                strokeWidth={2}
              />
              {index === last && (
                <text
                  x={x(index) + 9}
                  y={y(point.value)}
                  dy="0.32em"
                  className="fill-ink text-[11px] font-medium tabular-nums"
                >
                  {point.display}
                </text>
              )}
              <text x={x(index)} y={height - 22} textAnchor="middle" className="fill-ink-muted text-[11px]">
                {point.label[0]}
              </text>
              <text x={x(index)} y={height - 8} textAnchor="middle" className="fill-ink-muted text-[11px]">
                {point.label[1]}
              </text>
              {/* A generous, focusable target around each small marker. */}
              <circle
                cx={x(index)}
                cy={y(point.value)}
                r={14}
                fill="transparent"
                tabIndex={0}
                role="img"
                aria-label={`${point.name}: ${point.display}${point.detail ? `, ${point.detail}` : ""}`}
                onFocus={() => show(index)}
                onBlur={clear}
              />
            </g>
          ))}
        </svg>
      )}
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
