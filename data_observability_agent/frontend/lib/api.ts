const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface TableStatus {
  table_name: string;
  status: string;
  value?: number;
  observed_at?: string;
}

export interface DagStatus {
  dag_id: string;
  state: string;
  observed_at: string;
}

export interface StatusResponse {
  tables: TableStatus[];
  dags: DagStatus[];
}

export async function fetchStatus(): Promise<StatusResponse> {
  const res = await fetch(`${API_URL}/status`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch status");
  return res.json();
}
