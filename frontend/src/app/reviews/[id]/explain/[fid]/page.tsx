import Link from "next/link";
import { notFound } from "next/navigation";
import { getExplain } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ExplainPage({
  params,
}: {
  params: Promise<{ id: string; fid: string }>;
}) {
  const { id, fid } = await params;
  let data;
  try {
    data = await getExplain(id, fid);
  } catch {
    notFound();
  }

  const finding = data.finding as Record<string, unknown>;
  const review = data.review as Record<string, unknown>;

  return (
    <div>
      <p className="muted">
        <Link href={`/reviews/${id}`}>← Review</Link>
      </p>
      <h1>Why this finding exists</h1>
      <div className="card">
        <p>
          <span className={`severity-${finding.severity}`}>{String(finding.severity)}</span>{" "}
          · <strong>{String(finding.category)}</strong> — {String(finding.summary)}
        </p>
        <p className="mono">
          {String(finding.file_path)}:{String(finding.line_start ?? "?")}
          {finding.line_end && finding.line_end !== finding.line_start
            ? `-${String(finding.line_end)}`
            : ""}
          {" · "}
          confidence {finding.confidence !== null && finding.confidence !== undefined
            ? Number(finding.confidence).toFixed(2)
            : "—"}
        </p>
        <p className="muted">
          Review {String(review.id)} · {String(review.repo)} #{String(review.pr_number ?? "—")} ·{" "}
          {String(review.status)}
        </p>
      </div>

      <h2>Prompt versions that produced it</h2>
      <div className="card">
        {data.prompt_versions.length === 0 ? (
          <p className="muted">No prompt version recorded.</p>
        ) : (
          <ul>
            {data.prompt_versions.map((v) => (
              <li key={v} className="mono">
                {v}
              </li>
            ))}
          </ul>
        )}
      </div>

      <h2>Decision events</h2>
      <div className="card">
        {data.decision_events.length === 0 ? (
          <p className="muted">No decision events.</p>
        ) : (
          <ul className="timeline">
            {data.decision_events.map((e, i) => (
              <li key={i}>
                <span className="ev-type">{String(e.agent)}</span>{" "}
                <span className="muted">· {String(e.outcome ?? e.event_type)}</span>
                <div className="muted mono">{JSON.stringify(e.payload)}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
