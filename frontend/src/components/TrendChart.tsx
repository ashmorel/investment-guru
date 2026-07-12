import type { TrendSeries } from "../lib/types";

// Pure presentational inline-SVG multi-line chart — no charting library.
// Renders one <polyline> per series in its own color, scaled to a fixed
// viewBox from the min/max of the plotted metric across all series/points.
// `metric` picks which field of each point is plotted (value_base or pct).

const WIDTH = 600;
const HEIGHT = 220;
const PAD_X = 12;
const PAD_Y = 12;

function fieldFor(metric: "value" | "pct"): "value_base" | "pct" {
  return metric === "value" ? "value_base" : "pct";
}

export default function TrendChart({
  series,
  metric,
}: {
  series: TrendSeries[];
  metric: "value" | "pct";
}) {
  const field = fieldFor(metric);
  const allPoints = series.flatMap((s) => s.points);

  if (allPoints.length === 0) {
    return (
      <p className="text-sm text-muted">History is building — check back tomorrow.</p>
    );
  }

  const dates = Array.from(new Set(allPoints.map((p) => p.as_of))).sort();
  const values = allPoints.map((p) => Number(p[field]));
  const min = Math.min(0, ...values);
  const max = Math.max(...values, min + 1);

  const xFor = (asOf: string) => {
    if (dates.length <= 1) return WIDTH / 2;
    const idx = dates.indexOf(asOf);
    return PAD_X + (idx / (dates.length - 1)) * (WIDTH - PAD_X * 2);
  };
  const yFor = (v: number) =>
    HEIGHT - PAD_Y - ((v - min) / (max - min)) * (HEIGHT - PAD_Y * 2);

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      className="w-full"
      preserveAspectRatio="none"
    >
      <title>Group {metric === "value" ? "value" : "weight"} trend</title>
      {series.map((s) => (
        <polyline
          key={s.name}
          points={s.points.map((p) => `${xFor(p.as_of)},${yFor(Number(p[field]))}`).join(" ")}
          fill="none"
          stroke={s.color || "#94a3b8"}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ))}
    </svg>
  );
}
