import Link from "next/link";
import { Suspense } from "react";
import { listReviews, type ReviewSummary } from "@/lib/api";

const statusPill = (status: string) => (
  <span className={`pill ${status}`}>{status}</span>
);

async function ReviewTable() {
  const { reviews } = await listReviews();
  if (reviews.length === 0) {
    return <p className="muted">No reviews yet — fire a webhook and watch it land here.</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Repo</th>
          <th>PR</th>
          <th>Status</th>
          <th>Confidence</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {reviews.map((r: ReviewSummary) => (
          <tr key={r.id}>
            <td>
              <Link href={`/reviews/${r.id}`}>{r.repo}</Link>
            </td>
            <td>#{r.pr_number ?? "—"}</td>
            <td>{statusPill(r.status)}</td>
            <td className="mono">
              {r.overall_confidence !== null ? r.overall_confidence.toFixed(3) : "—"}
            </td>
            <td className="muted">{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Home() {
  return (
    <div>
      <h1>Recent reviews</h1>
      {/* RSC streaming boundary: the skeleton renders while the list streams */}
      <Suspense
        fallback={
          <div className="card">
            <div className="skeleton" />
            <div className="skeleton" />
            <div className="skeleton" />
          </div>
        }
      >
        <ReviewTable />
      </Suspense>
    </div>
  );
}
