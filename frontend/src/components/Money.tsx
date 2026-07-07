export default function Money({
  value,
  ccy,
  signed = false,
}: {
  value: string | null;
  ccy?: string;
  signed?: boolean;
}) {
  if (value === null) return <span className="text-muted">—</span>;
  const n = Number(value);
  const cls = signed ? (n > 0 ? "text-gain" : n < 0 ? "text-loss" : "") : "";
  const formatted = n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return (
    <span className={`tabular-nums ${cls}`}>
      {signed && n > 0 ? "+" : ""}
      {formatted}
      {ccy ? ` ${ccy}` : ""}
    </span>
  );
}
