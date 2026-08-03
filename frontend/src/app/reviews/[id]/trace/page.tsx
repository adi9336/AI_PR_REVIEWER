import Link from "next/link";
import { notFound } from "next/navigation";
import { getTrace } from "@/lib/api";

export const dynamic = "force-dynamic";

const eventLabel = (type: string) => {
  switch (type) {
    case "llm.call":
      return "LLM call";
    case "decision":
      return "Decision";
    case "tool.call":
      return "Tool call";
    case "span.start":
      return "Span start";
    case "span.end":
      return "Span end";
    default:
      return type;
  }
};

export default async function TracePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let trace;
  try {
    trace = await getTrace(id);
  } catch {
    notFound();
  }

  return (
    <div>
      <p className="muted">
        <Link href={`/reviews/${id}`}>← Review</Link>
      </p>
      <h1>Trace</h1>
      <p className="muted mono">{id}</p>
      <div className="card">
        <ul className="timeline">
          {trace.events.map((e, i) => (
            <li key={i}>
              <span className="ev-type">{eventLabel(e.event_type)}</span>{" "}
              <span className="muted">· {e.agent}</span>
              <div className="muted mono">
                {new Date(e.ts).toLocaleTimeString()}
                {e.model ? ` · ${e.model}` : ""}
                {e.cost_usd !== null ? ` · $${e.cost_usd.toFixed(6)}` : ""}
                {e.latency_ms !== null ? ` · ${e.latency_ms}ms` : ""}
                {e.outcome ? ` · ${e.outcome}` : ""}
              </div>
              {e.payload && (
                <div className="muted mono">{JSON.stringify(e.payload)}</div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
