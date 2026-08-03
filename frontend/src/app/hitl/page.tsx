import Link from "next/link";
import { getHitlQueue } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HitlPage() {
  const queue = await getHitlQueue();

  return (
    <div>
      <h1>HITL queue</h1>
      {queue.length === 0 ? (
        <p className="muted">Queue is empty — nothing awaiting human review.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Review</th>
              <th>Reason</th>
              <th>State</th>
              <th>Queued</th>
            </tr>
          </thead>
          <tbody>
            {queue.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link href={`/reviews/${item.review_id}`}>
                    {item.review_id.slice(0, 8)}…
                  </Link>
                </td>
                <td>{item.reason}</td>
                <td>
                  <span className={`pill ${item.state}`}>{item.state}</span>
                </td>
                <td className="muted">
                  {new Date(item.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
