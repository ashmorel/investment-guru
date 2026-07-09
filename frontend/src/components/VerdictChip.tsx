import type { Conviction, GuruAction, OrsoAction } from "../lib/types";

type ChipAction = GuruAction | OrsoAction;

const TONE: Record<ChipAction, string> = {
  increase: "bg-gain/10 text-gain",
  hold: "border border-border bg-bg text-muted",
  keep: "border border-border bg-bg text-muted",
  reduce: "bg-loss/10 text-loss",
  exit: "bg-loss/10 text-loss",
};

export default function VerdictChip({
  action,
  conviction,
  symbol,
}: {
  action: ChipAction;
  conviction: Conviction;
  symbol?: string;
}) {
  const label = symbol
    ? `${action.toUpperCase()} · ${symbol} · ${conviction.toUpperCase()}`
    : `${action.toUpperCase()} · ${conviction.toUpperCase()}`;

  return (
    <span
      className={`inline-block shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE[action]}`}
    >
      {label}
    </span>
  );
}
