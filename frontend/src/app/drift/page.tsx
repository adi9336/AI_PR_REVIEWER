import { notFound } from "next/navigation";
import { getDrift } from "@/lib/api";

export const dynamic = "force-dynamic";

const metricLabels: Record<string, string> = {
  cost_per_review: "Cost per review",
  avg_llm_latency_ms: "Avg LLM latency (ms)",
  llm_calls_per_review: "LLM calls per review",
  error_events: "Error events",
  findings_per_review: "Findings per review",
};

const badDirection = (direction: string) =>
  direction === "up" ? "↑ (rising is bad)" : "↓ (falling is bad)";

export default async function DriftPage() {
  let report;
  try {
    report = await getDrift();
  } catch {
    notFound();
  }

  return (
    <div>
      <h1>Drift — continuous learning</h1>
      <p className="muted">
        Window {report.window_days}d vs baseline {report.baseline_days}d ·
        threshold {report.threshold_pct}% · baseline reviews{" "}
        {report.baseline_reviews} (min {report.min_baseline_reviews})
      </p>
      <div className={`card ${report.any_drift ? "" : ""}`}>
        <p>
          Verdict:{" "}
          <strong style={{ color: report.any_drift ? "#f85149" : "#3fb950" }}>
            {report.any_drift ? "DRIFT DETECTED" : "NO DRIFT"}
          </strong>
        </p>
      </div>
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th>Direction</th>
            <th>Window</th>
            <th>Baseline</th>
            <th>Δ%</th>
            <th>Flag</th>
          </tr>
        </thead>
        <tbody>
          {report.metrics.map((m) => (
            <tr key={m.metric}>
              <td>{metricLabels[m.metric] ?? m.metric}</td>
              <td className="muted">{badDirection(m.direction)}</td>
              <td className="mono">{m.window_value?.toFixed(4) ?? "—"}</td>
              <td className="mono">{m.baseline_value?.toFixed(4) ?? "—"}</td>
              <td className="mono">
                {m.delta_pct !== null ? `${m.delta_pct > 0 ? "+" : ""}${m.delta_pct}%` : "—"}
              </td>
              <td>
                {m.drifted ? (
                  <span className="pill escalated">DRIFT</span>
                ) : (
                  <span className="pill completed">ok</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
