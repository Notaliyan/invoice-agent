"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import BarList, { type BarDatum } from "@/components/charts/BarList";
import ChartCard from "@/components/charts/ChartCard";
import ColumnChart, { type ColumnDatum } from "@/components/charts/ColumnChart";
import LineChart, { type LinePoint } from "@/components/charts/LineChart";
import StatTile from "@/components/charts/StatTile";
import { API_URL, fetchAnalytics, type Analytics } from "@/lib/api";
import { compact, date, dayLabel, money, monthLabel, niceTicks, percent, seconds } from "@/lib/format";

/** Every month from the first to the last, so a quiet month shows as a gap. */
function monthRange(first: string, last: string): string[] {
  const out: string[] = [];
  let [year, month] = first.split("-").map(Number) as [number, number];
  const [endYear, endMonth] = last.split("-").map(Number) as [number, number];
  while (year < endYear || (year === endYear && month <= endMonth)) {
    out.push(`${year}-${String(month).padStart(2, "0")}`);
    month += 1;
    if (month > 12) {
      month = 1;
      year += 1;
    }
    if (out.length > 120) break; // a malformed date must not loop forever
  }
  return out;
}

export default function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currency, setCurrency] = useState<string | null>(null);

  // One request, used by the first load and by the Refresh button. State is
  // only set from the promise callbacks, after the request settles.
  const request = useCallback((signal?: AbortSignal) => {
    fetchAnalytics(signal)
      .then((loaded) => {
        setData(loaded);
        setError(null);
      })
      .catch((problem: unknown) => {
        if (signal?.aborted) return;
        setError(
          problem instanceof TypeError
            ? `Could not reach the API at ${API_URL}. Is it running?`
            : problem instanceof Error
              ? problem.message
              : String(problem),
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    request(controller.signal);
    return () => controller.abort();
  }, [request]);

  const refresh = useCallback(() => {
    setLoading(true);
    request();
  }, [request]);

  const view = useMemo(() => (data ? shape(data) : null), [data]);
  const selected = currency ?? view?.currencies[0] ?? null;

  if (!view || !data) {
    return (
      <div>
        <h1 className="text-2xl font-semibold text-ink">Analytics</h1>
        {error ? (
          <div role="alert" className="mt-6 rounded-xl border border-critical/40 bg-critical/10 p-5">
            <p className="text-sm font-semibold text-ink">The analytics could not be loaded</p>
            <p className="mt-1 text-sm text-ink-secondary">{error}</p>
            <button
              type="button"
              onClick={refresh}
              className="mt-3 rounded-md border border-axis px-3 py-1.5 text-sm text-ink hover:bg-raised"
            >
              Try again
            </button>
          </div>
        ) : (
          <p className="mt-6 text-sm text-ink-muted">Loading…</p>
        )}
      </div>
    );
  }

  const spend = view.spend(selected);
  const monthly = view.monthly(selected);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Analytics</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Every figure comes from a SQL query over the stored runs.
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="rounded-md border border-axis px-3 py-1.5 text-sm text-ink hover:bg-raised disabled:opacity-60"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <p role="alert" className="mt-4 text-sm text-critical">
          Refresh failed, showing the last loaded data. {error}
        </p>
      )}

      {/* While refreshing, the previous render stays in place, dimmed. */}
      <div className={`transition-opacity ${loading ? "opacity-60" : ""}`}>
        <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {view.tiles.map((tile) => (
            <StatTile key={tile.label} {...tile} />
          ))}
        </div>

        <h2 className="mt-10 text-base font-semibold text-ink">Processing and accuracy</h2>
        <div className="mt-3 grid items-start gap-4 lg:grid-cols-3">
          <ChartCard
            title="Processing volume"
            subtitle="Documents processed per day (UTC), uploads and eval runs together"
            empty={view.volume.length === 0}
            table={{
              columns: ["Day", "Runs"],
              rows: view.volume.map((d) => [d.name, d.value]),
            }}
          >
            <ColumnChart
              data={view.volume}
              label="Runs per day"
              formatTick={(value) => compact(value)}
            />
          </ChartCard>

          <ChartCard
            title="Extraction accuracy"
            subtitle={`Share of fields correct on the golden dataset, per eval run. Axis starts at ${view.accuracyTicks[0]}%`}
            empty={view.accuracy.length === 0}
            emptyMessage="No eval runs stored yet. Run python -m evals.run_eval."
            table={{
              columns: ["Eval run", "Field accuracy", "Documents"],
              rows: view.accuracy.map((p) => [p.name, p.display, p.detail ?? ""]),
            }}
          >
            <LineChart
              data={view.accuracy}
              label="Field accuracy per eval run"
              ticks={view.accuracyTicks}
              formatTick={(value) => `${value}%`}
            />
          </ChartCard>

          <ChartCard
            title="Hallucination rate"
            subtitle="Share of fields the agent invented, per eval run. Lower is better"
            empty={view.hallucination.length === 0}
            emptyMessage="No eval runs stored yet."
            table={{
              columns: ["Eval run", "Hallucination rate", "Documents"],
              rows: view.hallucination.map((p) => [p.name, p.display, p.detail ?? ""]),
            }}
          >
            <LineChart
              data={view.hallucination}
              label="Hallucination rate per eval run"
              ticks={view.hallucinationTicks}
              formatTick={(value) => `${value}%`}
            />
          </ChartCard>
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-ink">Spend</h2>
          {view.currencies.length > 0 && (
            <div role="radiogroup" aria-label="Currency" className="flex gap-1 rounded-lg bg-surface p-1">
              {view.currencies.map((code) => (
                <button
                  key={code}
                  type="button"
                  role="radio"
                  aria-checked={code === selected}
                  onClick={() => setCurrency(code)}
                  className={`rounded-md px-3 py-1 text-sm transition-colors ${
                    code === selected
                      ? "bg-raised font-medium text-ink"
                      : "text-ink-muted hover:text-ink"
                  }`}
                >
                  {code}
                </button>
              ))}
            </div>
          )}
        </div>
        <p className="mt-1 text-xs text-ink-muted">
          Amounts in different currencies are never added together, so pick one.
        </p>

        <div className="mt-3 grid items-start gap-4 lg:grid-cols-2">
          <ChartCard
            title="Spend by vendor"
            subtitle={`Invoices and receipts in ${selected ?? "—"}, largest first`}
            empty={spend.length === 0}
            table={{
              columns: ["Vendor", "Total spend", "Documents"],
              rows: spend.map((d) => [d.label, d.display, d.detail ?? ""]),
            }}
          >
            <BarList data={spend} label={`Spend by vendor in ${selected}`} />
          </ChartCard>

          <ChartCard
            title="Invoice value by month"
            subtitle={`Total value of documents issued each month, in ${selected ?? "—"}`}
            empty={monthly.length === 0}
            table={{
              columns: ["Month", "Total value", "Documents"],
              rows: monthly.map((d) => [d.name, d.display, d.detail ?? ""]),
            }}
          >
            <ColumnChart
              data={monthly}
              label={`Invoice value by month in ${selected}`}
              formatTick={(value) => compact(value)}
            />
          </ChartCard>
        </div>

        <h2 className="mt-10 text-base font-semibold text-ink">Needs review</h2>
        <p className="mt-1 text-xs text-ink-muted">
          Documents whose own numbers disagree. A gap of 0.00 means that check passed.
        </p>
        <div className="mt-3 overflow-x-auto rounded-xl border border-hairline bg-surface">
          {view.exceptions.length === 0 ? (
            <p className="p-5 text-sm text-ink-muted">
              <span aria-hidden="true" className="mr-1.5 font-bold text-good">
                ✓
              </span>
              Every stored document adds up.
            </p>
          ) : (
            <table className="w-full min-w-[40rem] text-sm">
              <thead>
                <tr className="text-left text-xs text-ink-muted">
                  <th scope="col" className="px-5 py-3 font-normal">Document</th>
                  <th scope="col" className="px-3 py-3 font-normal">Vendor</th>
                  <th scope="col" className="px-3 py-3 text-right font-normal">Total</th>
                  <th scope="col" className="px-3 py-3 text-right font-normal">Line items vs subtotal</th>
                  <th scope="col" className="px-5 py-3 text-right font-normal">Subtotal + tax vs total</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {view.exceptions.map((row) => (
                  <tr key={`${row.filename}-${row.invoice_number}`} className="border-t border-hairline">
                    <td className="px-5 py-3 text-ink">
                      <span aria-hidden="true" className="mr-1.5 font-bold text-warning">
                        !
                      </span>
                      {row.filename}
                      {row.invoice_number && (
                        <span className="block pl-4 text-xs text-ink-muted">{row.invoice_number}</span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-ink-secondary">{row.vendor}</td>
                    <td className="px-3 py-3 text-right text-ink">{money(row.total, row.currency)}</td>
                    <td className="px-3 py-3 text-right text-ink-secondary">
                      {money(row.line_item_gap, row.currency)}
                    </td>
                    <td className="px-5 py-3 text-right text-ink-secondary">
                      {money(row.total_gap, row.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <p className="mt-6 text-xs text-ink-muted">
          Loaded {new Date(data.generated_at).toLocaleString("en-GB")} from{" "}
          <span className="font-mono">{API_URL}/analytics</span>
        </p>
      </div>
    </div>
  );
}

/** Turn the API's query rows into what each chart draws. */
function shape(data: Analytics) {
  const q = data.queries;

  // ---- headline numbers ----
  const performance = q.processing_performance.rows;
  const runs = performance.reduce((sum, row) => sum + row.runs, 0);
  const succeeded = performance.reduce((sum, row) => sum + row.succeeded, 0);
  const timed = performance.reduce((sum, row) => sum + row.timed_runs, 0);
  const totalMs = performance.reduce(
    (sum, row) => sum + (row.avg_duration_ms ?? 0) * row.timed_runs,
    0,
  );
  const tiles = [
    { label: "Documents", value: data.tables.documents.toLocaleString("en-GB"), note: "Distinct files" },
    {
      label: "Processing runs",
      value: runs.toLocaleString("en-GB"),
      note: `${data.tables.line_items.toLocaleString("en-GB")} line items stored`,
    },
    {
      label: "Success rate",
      value: runs > 0 ? percent(succeeded / runs, 0) : "—",
      note: `${succeeded} of ${runs} runs produced an extraction`,
    },
    {
      label: "Average processing time",
      value: timed > 0 ? seconds(totalMs / timed) : "—",
      note: timed > 0 ? `Over ${timed} timed runs, rate-limit waits included` : "No timed runs yet",
    },
  ];

  // ---- processing volume: runs per day ----
  const quality = q.extraction_quality_over_time.rows;
  const perDay = new Map<string, number>();
  for (const row of quality) perDay.set(row.day, (perDay.get(row.day) ?? 0) + row.runs);
  const volume: ColumnDatum[] = [...perDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, count]) => ({
      label: dayLabel(day),
      name: date(day),
      value: count,
      display: `${count} ${count === 1 ? "run" : "runs"}`,
    }));

  // ---- accuracy and hallucination, one point per eval run ----
  const scored = quality.filter((row) => row.scored_runs > 0 && row.avg_field_accuracy !== null);
  const point = (row: (typeof scored)[number], value: number): LinePoint => ({
    label: [dayLabel(row.day), row.prompt_version || "—"],
    name: `${date(row.day)}, prompt ${row.prompt_version || "—"}`,
    value,
    display: `${value.toFixed(1)}%`,
    detail: `${row.scored_runs} documents`,
  });
  const accuracy = scored.map((row) => point(row, (row.avg_field_accuracy ?? 0) * 100));
  const hallucination = scored.map((row) => point(row, (row.avg_hallucination_rate ?? 0) * 100));

  // The accuracy axis is cut off below the data, and says so in the subtitle:
  // from zero, 97.8% and 99.4% would be the same line.
  const lowest = Math.min(...accuracy.map((p) => p.value), 100);
  const floor = Math.max(0, Math.floor((lowest - 0.001) / 5) * 5);
  const accuracyTicks = niceTicks(floor, 100, 5);
  const hallucinationTicks = niceTicks(0, Math.max(...hallucination.map((p) => p.value), 1), 4);

  // ---- spend, one currency at a time ----
  const count = new Map<string, number>();
  for (const row of q.spend_by_vendor.rows) {
    count.set(row.currency, (count.get(row.currency) ?? 0) + row.invoices);
  }
  for (const row of q.monthly_invoice_volume.rows) {
    if (!count.has(row.currency)) count.set(row.currency, 0);
  }
  // Most documents first; the first one is the default selection.
  const currencies = [...count.entries()]
    .sort(([a, x], [b, y]) => y - x || a.localeCompare(b))
    .map(([code]) => code);

  const spend = (code: string | null): BarDatum[] =>
    q.spend_by_vendor.rows
      .filter((row) => row.currency === code)
      .sort((a, b) => b.total_spend - a.total_spend)
      .map((row) => ({
        label: row.vendor,
        value: row.total_spend,
        display: money(row.total_spend, row.currency),
        detail: `${row.invoices} ${row.invoices === 1 ? "document" : "documents"}`,
      }));

  const monthly = (code: string | null): ColumnDatum[] => {
    const rows = q.monthly_invoice_volume.rows.filter((row) => row.currency === code);
    if (rows.length === 0 || !code) return [];
    const byMonth = new Map(rows.map((row) => [row.month, row]));
    const months = [...byMonth.keys()].sort();
    const range = monthRange(months[0] ?? "", months[months.length - 1] ?? "");
    const oneYear = new Set(range.map((m) => m.slice(0, 4))).size === 1;
    return range.map((month) => {
      const row = byMonth.get(month);
      const documents = row?.invoices ?? 0;
      return {
        label: monthLabel(month, !oneYear),
        name: monthLabel(month),
        value: row?.total_value ?? 0,
        display: money(row?.total_value ?? 0, code),
        detail: `${documents} ${documents === 1 ? "document" : "documents"}`,
      };
    });
  };

  return {
    tiles,
    volume,
    accuracy,
    accuracyTicks,
    hallucination,
    hallucinationTicks,
    currencies,
    spend,
    monthly,
    exceptions: q.arithmetic_exceptions.rows,
  };
}
