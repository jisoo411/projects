import type { DagStatus } from "@/lib/api";

const STATE_COLORS: Record<string, string> = {
  success: "text-green-600",
  running: "text-blue-600",
  failed: "text-red-600",
  queued: "text-yellow-600",
};

export function DagStatusList({ dags }: { dags: DagStatus[] }) {
  if (dags.length === 0) {
    return <p className="text-sm text-gray-500">No DAG status data available.</p>;
  }
  return (
    <ul className="divide-y text-sm">
      {dags.map((d) => (
        <li key={d.dag_id} className="py-2 flex items-center gap-3">
          <span className="font-mono flex-1">{d.dag_id}</span>
          <span className={`font-semibold ${STATE_COLORS[d.state] ?? ""}`}>{d.state}</span>
          <span className="text-gray-400 text-xs">{d.observed_at}</span>
        </li>
      ))}
    </ul>
  );
}
