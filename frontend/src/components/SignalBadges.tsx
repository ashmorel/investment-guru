import type { Signal } from "../lib/types";

const TONE: Record<Signal["severity"], string> = {
  high: "bg-[#FEECEC] text-loss",
  watch: "bg-[#FFFBEB] text-flag",
  info: "bg-bg text-muted",
};

function label(s: Signal): string {
  switch (s.kind) {
    case "earnings_upcoming":
      return `earnings ${s.data.days_until ?? ""}d`;
    case "price_move_day":
      return `${s.data.pct ?? ""}% today`;
    case "price_move_week":
      return `${s.data.pct ?? ""}% wk`;
    case "fifty_two_week":
      return s.title.includes("high") ? "52w high" : "52w low";
    case "unusual_volume":
      return `${s.data.mult ?? ""}x vol`;
    case "news_recent":
      return "news";
    default:
      return s.kind;
  }
}

export default function SignalBadges({ signals }: { signals: Signal[] }) {
  if (signals.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {signals.map((s) => (
        <span
          key={s.id}
          title={s.title}
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${TONE[s.severity]}`}
        >
          {label(s)}
        </span>
      ))}
    </span>
  );
}
