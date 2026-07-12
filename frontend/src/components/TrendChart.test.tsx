import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";
import TrendChart from "./TrendChart";

const TWO_SERIES = [
  {
    group_id: 1,
    name: "Tech",
    color: "#4f46e5",
    points: [
      { as_of: "2026-07-01", value_base: "100.00", pct: "50.00" },
      { as_of: "2026-07-02", value_base: "110.00", pct: "55.00" },
      { as_of: "2026-07-03", value_base: "120.00", pct: "52.00" },
    ],
  },
  {
    group_id: null,
    name: "Ungrouped",
    color: "#94a3b8",
    points: [
      { as_of: "2026-07-01", value_base: "100.00", pct: "50.00" },
      { as_of: "2026-07-02", value_base: "90.00", pct: "45.00" },
      { as_of: "2026-07-03", value_base: "110.00", pct: "48.00" },
    ],
  },
];

describe("TrendChart", () => {
  it("draws one polyline per series with the right number of points", () => {
    const { container } = render(<TrendChart series={TWO_SERIES} metric="value" />);
    const polylines = container.querySelectorAll("polyline");
    expect(polylines).toHaveLength(2);
    for (const pl of polylines) {
      const points = (pl.getAttribute("points") ?? "").trim().split(/\s+/).filter(Boolean);
      expect(points).toHaveLength(3);
    }
  });

  it("colors each polyline with its series color", () => {
    const { container } = render(<TrendChart series={TWO_SERIES} metric="value" />);
    const polylines = container.querySelectorAll("polyline");
    const strokes = Array.from(polylines).map((pl) => pl.getAttribute("stroke"));
    expect(strokes).toContain("#4f46e5");
    expect(strokes).toContain("#94a3b8");
  });

  it("exposes an accessible role=img with a title", () => {
    render(<TrendChart series={TWO_SERIES} metric="value" />);
    const svg = screen.getByRole("img");
    expect(svg.querySelector("title")).toHaveTextContent(/trend/i);
  });

  it("switches the plotted field when metric changes from value to pct", () => {
    const { container, rerender } = render(<TrendChart series={TWO_SERIES} metric="value" />);
    const valuePoints = container.querySelector("polyline")?.getAttribute("points");
    rerender(<TrendChart series={TWO_SERIES} metric="pct" />);
    const pctPoints = container.querySelector("polyline")?.getAttribute("points");
    expect(pctPoints).not.toEqual(valuePoints);
  });

  it("shows a history-is-building message when every series has zero points", () => {
    render(
      <TrendChart
        series={[
          { group_id: 1, name: "Tech", color: "#4f46e5", points: [] },
          { group_id: null, name: "Ungrouped", color: "#94a3b8", points: [] },
        ]}
        metric="value"
      />,
    );
    expect(screen.getByText(/history is building/i)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("has no detectable accessibility violations", async () => {
    const { container } = render(<TrendChart series={TWO_SERIES} metric="value" />);
    expect(await axe(container)).toHaveNoViolations();
  });
});
