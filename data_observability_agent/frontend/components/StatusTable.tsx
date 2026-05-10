import type { TableStatus } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  ok: "text-green-600",
  warn: "text-yellow-600",
  critical: "text-red-600",
  no_data: "text-gray-400",
};

export function StatusTable({ tables }: { tables: TableStatus[] }) {
  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="border-b text-left text-gray-500">
          <th className="py-2 pr-4">Table</th>
          <th className="py-2 pr-4">Score</th>
          <th className="py-2 pr-4">Status</th>
          <th className="py-2">Last Observed</th>
        </tr>
      </thead>
      <tbody>
        {tables.map((t) => (
          <tr key={t.table_name} className="border-b hover:bg-gray-50">
            <td className="py-2 pr-4 font-mono">{t.table_name}</td>
            <td className="py-2 pr-4">{t.value !== undefined ? t.value.toFixed(2) : "—"}</td>
            <td className={`py-2 pr-4 font-semibold ${STATUS_COLORS[t.status] ?? ""}`}>
              {t.status}
            </td>
            <td className="py-2 text-gray-500">{t.observed_at ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
