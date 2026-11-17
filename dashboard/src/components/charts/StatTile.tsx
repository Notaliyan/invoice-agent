interface StatTileProps {
  label: string;
  value: string;
  /** One line of context under the number. */
  note?: string;
}

/** A headline number. When the data is a single value, the number is the chart. */
export default function StatTile({ label, value, note }: StatTileProps) {
  return (
    <div className="rounded-xl border border-hairline bg-surface p-5">
      <p className="text-xs text-ink-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-ink">{value}</p>
      {note && <p className="mt-1 text-xs text-ink-muted">{note}</p>}
    </div>
  );
}
