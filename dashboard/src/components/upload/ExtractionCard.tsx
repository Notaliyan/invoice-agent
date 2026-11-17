import type { Party, ProcessResult } from "@/lib/api";
import { date, money, percent, seconds } from "@/lib/format";

const TYPE_LABELS: Record<string, string> = {
  invoice: "Invoice",
  receipt: "Receipt",
  purchase_order: "Purchase order",
  contract: "Contract",
  other: "Other document",
};

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  const missing = value === null || value === undefined || value === "" || value === "—";
  return (
    <div>
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className={`mt-0.5 break-words text-sm ${missing ? "text-ink-muted" : "text-ink"}`}>
        {missing ? "Not on the document" : value}
      </dd>
    </div>
  );
}

function PartyBlock({ title, party }: { title: string; party: Party | null }) {
  return (
    <div>
      <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">{title}</h3>
      {party ? (
        <div className="mt-1.5 text-sm">
          <p className="font-medium text-ink">{party.name}</p>
          {party.address && <p className="text-ink-secondary">{party.address}</p>}
          {party.tax_id && <p className="text-ink-muted">Tax ID {party.tax_id}</p>}
          {party.email && <p className="text-ink-muted">{party.email}</p>}
        </div>
      ) : (
        <p className="mt-1.5 text-sm text-ink-muted">Not on the document</p>
      )}
    </div>
  );
}

/** A status chip: an icon and a word, never colour alone. */
function Chip({ tone, children }: { tone: "good" | "warning" | "neutral"; children: React.ReactNode }) {
  const styles = {
    good: "border-good/30 bg-good/10 text-ink",
    warning: "border-warning/30 bg-warning/10 text-ink",
    neutral: "border-hairline bg-raised text-ink-secondary",
  }[tone];
  const icon = { good: "✓", warning: "!", neutral: null }[tone];
  const iconColour = tone === "good" ? "text-good" : "text-warning";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${styles}`}>
      {icon && (
        <span aria-hidden="true" className={`font-bold ${iconColour}`}>
          {icon}
        </span>
      )}
      {children}
    </span>
  );
}

/** Placeholder with the card's shape, shown while the agent is still working. */
export function ExtractionSkeleton({ running }: { running: boolean }) {
  return (
    <section
      aria-label="Extracted fields"
      className="rounded-xl border border-hairline bg-surface p-5"
    >
      <h2 className="text-sm font-semibold text-ink">Extracted fields</h2>
      <p className="mt-1 text-sm text-ink-muted">
        {running
          ? "The fields appear here once the agent has checked its numbers and submitted them."
          : "Upload a document to see what the agent extracts from it."}
      </p>
      <div className={`mt-5 grid grid-cols-2 gap-4 ${running ? "animate-pulse" : ""}`} aria-hidden="true">
        {["w-3/4", "w-1/2", "w-2/3", "w-1/3", "w-1/2", "w-3/5"].map((width, index) => (
          <div key={index}>
            <div className="h-2.5 w-16 rounded bg-raised" />
            <div className={`mt-2 h-4 rounded bg-raised ${width}`} />
          </div>
        ))}
      </div>
    </section>
  );
}

export default function ExtractionCard({ result }: { result: ProcessResult }) {
  const { extraction, validation, trace } = result;
  const currency = extraction.currency;
  const failedChecks = validation.checks.filter((check) => !check.passed);

  return (
    <section aria-label="Extracted fields" className="rise rounded-xl border border-hairline bg-surface">
      <div className="border-b border-hairline p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">Extracted fields</h2>
            <p className="mt-0.5 text-xs text-ink-muted">{result.filename}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Chip tone="neutral">{TYPE_LABELS[extraction.document_type] ?? extraction.document_type}</Chip>
            {validation.passed ? (
              <Chip tone="good">Arithmetic checks passed</Chip>
            ) : (
              <Chip tone="warning">Needs review</Chip>
            )}
          </div>
        </div>

        <p className="mt-4 text-3xl font-semibold text-ink">{money(extraction.total, currency)}</p>
        <p className="text-xs text-ink-muted">Total</p>
      </div>

      <div className="grid gap-6 border-b border-hairline p-5 sm:grid-cols-2">
        <PartyBlock title="Vendor" party={extraction.vendor} />
        <PartyBlock title="Customer" party={extraction.customer} />
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border-b border-hairline p-5 sm:grid-cols-3">
        <Field label="Document number" value={extraction.invoice_number} />
        <Field label="Issue date" value={date(extraction.issue_date)} />
        <Field label="Due date" value={date(extraction.due_date)} />
        <Field label="Subtotal" value={money(extraction.subtotal, currency)} />
        <Field
          label={extraction.tax_rate !== null ? `Tax (${extraction.tax_rate}%)` : "Tax"}
          value={money(extraction.tax_amount, currency)}
        />
        <Field label="Currency" value={currency} />
      </dl>

      {extraction.line_items.length > 0 && (
        <div className="border-b border-hairline p-5">
          <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">Line items</h3>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[26rem] text-sm">
              <thead>
                <tr className="text-left text-xs text-ink-muted">
                  <th scope="col" className="py-1.5 pr-3 font-normal">Description</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-normal">Qty</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-normal">Unit price</th>
                  <th scope="col" className="py-1.5 text-right font-normal">Amount</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {extraction.line_items.map((item, index) => (
                  <tr key={index} className="border-t border-hairline">
                    <td className="py-2 pr-3 text-ink">{item.description}</td>
                    <td className="py-2 pr-3 text-right text-ink-secondary">{item.quantity ?? "—"}</td>
                    <td className="py-2 pr-3 text-right text-ink-secondary">
                      {money(item.unit_price, currency)}
                    </td>
                    <td className="py-2 text-right text-ink">{money(item.amount, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="border-b border-hairline p-5">
        <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
          Arithmetic checks
        </h3>
        {validation.checks.length === 0 ? (
          <p className="mt-2 text-sm text-ink-muted">No checks were recorded for this document.</p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {validation.checks.map((check) => (
              <li key={check.name} className="flex gap-2 text-sm">
                <span
                  aria-hidden="true"
                  className={`font-bold ${check.passed ? "text-good" : "text-warning"}`}
                >
                  {check.passed ? "✓" : "!"}
                </span>
                <span className="min-w-0">
                  <span className="sr-only">{check.passed ? "Passed: " : "Failed: "}</span>
                  <span className="font-mono text-xs text-ink-secondary">{check.name}</span>
                  <span className="block break-words text-xs text-ink-muted">{check.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {failedChecks.length > 0 && extraction.notes && (
          <p className="mt-3 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-ink-secondary">
            <span className="font-medium text-ink">The agent&apos;s note: </span>
            {extraction.notes}
          </p>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 p-5 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-ink-muted">Took</dt>
          <dd className="mt-0.5 text-ink">{seconds(result.duration_ms)}</dd>
        </div>
        <div>
          <dt className="text-ink-muted">Model steps</dt>
          <dd className="mt-0.5 text-ink">{trace.steps}</dd>
        </div>
        <div>
          <dt className="text-ink-muted">Model&apos;s confidence</dt>
          <dd className="mt-0.5 text-ink">{percent(extraction.confidence, 0)}</dd>
        </div>
        <div>
          <dt className="text-ink-muted">Stored as</dt>
          <dd className="mt-0.5 text-ink">
            {result.run_id !== null ? `Run ${result.run_id}` : "Not stored"}
          </dd>
        </div>
      </dl>
    </section>
  );
}
