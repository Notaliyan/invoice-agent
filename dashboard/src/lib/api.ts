/**
 * The shapes the API returns, and where to find it.
 *
 * NEXT_PUBLIC_API_URL is the API's address as the browser sees it. It is
 * compiled in at build time (see the Dockerfile).
 */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

// ---- /process -------------------------------------------------------------

export interface Party {
  name: string;
  address: string | null;
  tax_id: string | null;
  email: string | null;
}

export interface LineItem {
  description: string;
  quantity: number | null;
  unit_price: number | null;
  amount: number;
}

export interface Extraction {
  document_type: string;
  vendor: Party;
  customer: Party | null;
  invoice_number: string | null;
  issue_date: string | null;
  due_date: string | null;
  currency: string;
  line_items: LineItem[];
  subtotal: number | null;
  tax_amount: number | null;
  tax_rate: number | null;
  total: number;
  confidence: number;
  notes: string | null;
}

export interface ValidationCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ProcessResult {
  document_id: string;
  run_id: number | null;
  filename: string;
  pages: number;
  extraction: Extraction;
  validation: { passed: boolean; checks: ValidationCheck[] };
  trace: {
    model: string;
    prompt_version: string;
    steps: number;
    prompt_tokens: number;
    completion_tokens: number;
  };
  duration_ms: number;
}

/** What `/process/stream` sends while the agent works. */
export type ProgressEvent =
  | { type: "step"; step: number; max_steps: number }
  | {
      type: "tool";
      step: number;
      tool: string;
      ok: boolean;
      summary: string;
      duration_ms: number;
    };

export interface StartedEvent {
  filename: string;
  pages: number;
  chars: number;
}

// ---- /analytics -----------------------------------------------------------

export interface SpendRow {
  vendor: string;
  currency: string;
  invoices: number;
  total_spend: number;
  average_invoice: number;
  first_invoice: string | null;
  last_invoice: string | null;
}

export interface MonthlyRow {
  month: string;
  currency: string;
  invoices: number;
  total_value: number;
  total_tax: number;
}

export interface PerformanceRow {
  model: string;
  prompt_version: string;
  runs: number;
  succeeded: number;
  timed_runs: number;
  avg_duration_ms: number | null;
  fastest_ms: number | null;
  slowest_ms: number | null;
  avg_steps: number | null;
  avg_tokens: number | null;
}

export interface QualityRow {
  day: string;
  prompt_version: string;
  model: string;
  runs: number;
  success_rate: number;
  validation_pass_rate: number;
  avg_confidence: number | null;
  scored_runs: number;
  avg_field_accuracy: number | null;
  avg_completeness: number | null;
  avg_hallucination_rate: number | null;
}

export interface ExceptionRow {
  filename: string;
  vendor: string;
  invoice_number: string | null;
  currency: string;
  subtotal: number | null;
  tax_amount: number | null;
  total: number;
  line_item_sum: number | null;
  line_item_gap: number | null;
  total_gap: number | null;
}

interface QueryResult<Row> {
  description: string;
  sql: string;
  rows: Row[];
}

export interface Analytics {
  generated_at: string;
  tables: {
    documents: number;
    processing_runs: number;
    invoices: number;
    line_items: number;
  };
  queries: {
    spend_by_vendor: QueryResult<SpendRow>;
    monthly_invoice_volume: QueryResult<MonthlyRow>;
    processing_performance: QueryResult<PerformanceRow>;
    extraction_quality_over_time: QueryResult<QualityRow>;
    arithmetic_exceptions: QueryResult<ExceptionRow>;
  };
}

export async function fetchAnalytics(signal?: AbortSignal): Promise<Analytics> {
  const response = await fetch(`${API_URL}/analytics?limit=50`, { signal, cache: "no-store" });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as Analytics;
}

/** The API puts a human-readable reason in `detail`; fall back to the status. */
export async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON; fall through
  }
  return `The API answered ${response.status} ${response.statusText}`.trim();
}
