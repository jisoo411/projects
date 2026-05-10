import { fetchStatus } from "@/lib/api";
import { StatusTable } from "@/components/StatusTable";
import { DagStatusList } from "@/components/DagStatusList";
import { ChatPanel } from "@/components/ChatPanel";

export default async function Dashboard() {
  let status = null;
  try {
    status = await fetchStatus();
  } catch {
    // Backend not available — render empty state
  }

  const apiKey = process.env.API_KEY ?? "";

  return (
    <main className="min-h-screen p-8 max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Pipeline Observability Dashboard</h1>

      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Data Quality</h2>
        {status ? (
          <StatusTable tables={status.tables} />
        ) : (
          <p className="text-sm text-gray-500">Status unavailable.</p>
        )}
      </section>

      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Pipeline Status</h2>
        {status ? (
          <DagStatusList dags={status.dags} />
        ) : (
          <p className="text-sm text-gray-500">Status unavailable.</p>
        )}
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Chat</h2>
        <ChatPanel apiKey={apiKey} />
      </section>
    </main>
  );
}
