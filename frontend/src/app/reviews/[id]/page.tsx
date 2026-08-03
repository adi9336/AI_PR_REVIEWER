import Link from "next/link";
import { notFound } from "next/navigation";
import { getReview } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let detail;
  try {
    detail = await getReview(id);
  } catch {
    notFound();
  }

  const { review, findings } = detail;

  return (
    <div>
      <p className="muted">
        <Link href="/">← Reviews</Link>
      </p>
      <h1>
        {review.repo} #{review.pr_number ?? "—"}
      </h1>
      <div className="card">
        <p>
          Status: <span className={`pill ${review.status}`}>{review.status}</span>{" "}
          <span className="muted">
            · confidence{" "}
            {review.overall_confidence !== null
              ? review.overall_confidence.toFixed(3)
              : "—"}{" "}
            · {detail.events_count} events ·{" "}
            <Link href={`/reviews/${review.id}/trace`}>trace</Link>
          </span>
        </p>
        <p className="muted mono">
          {review.id} · created{" "}
          {review.created_at ? new Date(review.created_at).toLocaleString() : "—"}
        </p>
      </div>

      <h2>Findings ({findings.length})</h2>
      {findings.length === 0 ? (
        <p className="muted">No findings.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Severity</th>
              <th>Agent</th>
              <th>Category</th>
              <th>Location</th>
              <th>Summary</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {findings.map((f) => (
              <tr key={f.id}>
                <td className={`severity-${f.severity}`}>{f.severity}</td>
                <td>{f.agent_type}</td>
                <td>{f.category}</td>
                <td className="mono">
                  {f.file_path}:{f.line_start ?? "?"}
                  {f.line_end && f.line_end !== f.line_start ? `-${f.line_end}` : ""}
                </td>
                <td>{f.summary}</td>
                <td className="mono">
                  {f.confidence !== null ? f.confidence.toFixed(2) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
