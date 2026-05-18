# Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Next.js frontend — a Dashboard page showing live table/DAG status, a ChatPanel with SSE streaming and a degraded-reranking banner, and a source URI resolver that converts `airflow:` and `dbcheck:` URIs into deep-links. Forwards `X-API-Key` header on every request.

**Architecture:** Next.js 14 App Router. `Dashboard` server-fetches `/status` at build-time with `cache: "no-store"`. `ChatPanel` is a client component that opens a `fetch()` stream to `/chat` and renders `EventSource`-style chunks. Source URIs are resolved to human-readable labels client-side. `NEXT_PUBLIC_API_URL` and `API_KEY` set via Vercel env vars.

**Tech Stack:** Next.js 14, React 18, TypeScript, Tailwind CSS

---

## File Map

```
frontend/
├── app/
│   ├── layout.tsx
│   ├── page.tsx              # Dashboard (server component)
│   └── globals.css
├── components/
│   ├── ChatPanel.tsx         # client component — SSE stream + degraded banner
│   ├── StatusTable.tsx       # table quality rows
│   ├── DagStatusList.tsx     # DAG status rows
│   └── SourceLink.tsx        # URI → readable label + link
├── lib/
│   └── api.ts                # typed fetch helpers
└── __tests__/
    ├── ChatPanel.test.tsx
    └── SourceLink.test.tsx
```

---

### Task 1: Bootstrap the Next.js Project

**Files:**
- Modify: `frontend/` (from `npx create-next-app`)

- [ ] **Step 1: Scaffold Next.js app**

```bash
cd frontend
npx create-next-app@14 . --typescript --tailwind --app --no-src-dir --import-alias "@/*"
```

- [ ] **Step 2: Install test dependencies**

```bash
cd frontend && npm install -D jest @testing-library/react @testing-library/jest-dom jest-environment-jsdom @types/jest ts-jest
```

- [ ] **Step 3: Add `jest.config.ts`**

```typescript
// frontend/jest.config.ts
import type { Config } from "jest";

const config: Config = {
  preset: "ts-jest",
  testEnvironment: "jsdom",
  setupFilesAfterFramework: ["<rootDir>/jest.setup.ts"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/$1",
  },
};

export default config;
```

- [ ] **Step 4: Add `jest.setup.ts`**

```typescript
// frontend/jest.setup.ts
import "@testing-library/jest-dom";
```

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "chore: scaffold Next.js 14 app with TypeScript, Tailwind, Jest"
```

---

### Task 2: Write Failing Tests

**Files:**
- Create: `frontend/__tests__/SourceLink.test.tsx`
- Create: `frontend/__tests__/ChatPanel.test.tsx`

- [ ] **Step 1: Write `frontend/__tests__/SourceLink.test.tsx`**

```typescript
import { render, screen } from "@testing-library/react";
import { SourceLink } from "@/components/SourceLink";

test("resolves airflow URI to readable label", () => {
  render(
    <SourceLink uri="airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z" />
  );
  expect(screen.getByText(/orders_pipeline/)).toBeInTheDocument();
  expect(screen.getByText(/extract_orders/)).toBeInTheDocument();
});

test("resolves dbcheck URI to readable label", () => {
  render(
    <SourceLink uri="dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z" />
  );
  expect(screen.getByText(/orders/)).toBeInTheDocument();
  expect(screen.getByText(/composite/)).toBeInTheDocument();
});

test("renders unknown URI scheme as plain text", () => {
  render(<SourceLink uri="unknown:some/path" />);
  expect(screen.getByText("unknown:some/path")).toBeInTheDocument();
});
```

- [ ] **Step 2: Write `frontend/__tests__/ChatPanel.test.tsx`**

```typescript
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPanel } from "@/components/ChatPanel";

// Mock global fetch to return an SSE stream
function mockSseResponse(chunks: string[]) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(`data: ${chunk}\n\n`));
      }
      controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
});

test("renders chat input and submit button", () => {
  render(<ChatPanel apiKey="test-key" />);
  expect(screen.getByPlaceholderText(/ask about/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /send/i })).toBeInTheDocument();
});

test("streams response and displays it", async () => {
  global.fetch = jest.fn().mockResolvedValueOnce(
    mockSseResponse(["orders_pipeline failed at extract_orders."])
  );
  render(<ChatPanel apiKey="test-key" />);
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), "pipeline status?");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() => {
    expect(screen.getByText(/orders_pipeline failed/)).toBeInTheDocument();
  });
});

test("shows degraded reranking banner when stream contains flag", async () => {
  global.fetch = jest.fn().mockResolvedValueOnce(
    mockSseResponse(["[DEGRADED_RERANKING]", "orders ok."])
  );
  render(<ChatPanel apiKey="test-key" />);
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), "pipeline status?");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() => {
    expect(screen.getByText(/reranking/i)).toBeInTheDocument();
  });
});

test("sends X-API-Key header", async () => {
  global.fetch = jest.fn().mockResolvedValueOnce(mockSseResponse(["ok."]));
  render(<ChatPanel apiKey="my-api-key" />);
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), "q");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  const [, init] = (global.fetch as jest.Mock).mock.calls[0];
  expect((init as RequestInit).headers?.["X-API-Key"]).toBe("my-api-key");
});
```

- [ ] **Step 3: Run to verify they fail**

```bash
cd frontend && npx jest
```

Expected: FAIL — components not created yet.

---

### Task 3: Implement Frontend Components

**Files:**
- Create: `frontend/lib/api.ts`
- Create: `frontend/components/SourceLink.tsx`
- Create: `frontend/components/ChatPanel.tsx`
- Create: `frontend/components/StatusTable.tsx`
- Create: `frontend/components/DagStatusList.tsx`
- Modify: `frontend/app/page.tsx`

- [ ] **Step 1: Write `frontend/lib/api.ts`**

```typescript
// frontend/lib/api.ts
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
```

- [ ] **Step 2: Write `frontend/components/SourceLink.tsx`**

```typescript
// frontend/components/SourceLink.tsx
"use client";

interface SourceLinkProps {
  uri: string;
}

function parseUri(uri: string): string {
  if (uri.startsWith("airflow:")) {
    const params = Object.fromEntries(
      uri.slice("airflow:".length).split("/").map((p) => p.split("=") as [string, string])
    );
    return `Airflow › ${params.dag ?? "?"} › ${params.task ?? "?"}`;
  }
  if (uri.startsWith("dbcheck:")) {
    const params = Object.fromEntries(
      uri.slice("dbcheck:".length).split("/").map((p) => p.split("=") as [string, string])
    );
    return `DB Check › ${params.table ?? "?"} › ${params.metric ?? "?"}`;
  }
  return uri;
}

export function SourceLink({ uri }: SourceLinkProps) {
  return (
    <span className="text-xs text-blue-600 underline cursor-pointer" title={uri}>
      {parseUri(uri)}
    </span>
  );
}
```

- [ ] **Step 3: Write `frontend/components/ChatPanel.tsx`**

```typescript
// frontend/components/ChatPanel.tsx
"use client";
import { useState, useRef } from "react";
import { SourceLink } from "./SourceLink";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const AIRFLOW_URI_RE = /airflow:dag=[^\s,]+\/task=[^\s,]+\/run=[^\s,]+\/try=\d+\/ts=\S+/g;
const DBCHECK_URI_RE = /dbcheck:table=[^\s,]+\/metric=[^\s,]+\/ts=\S+/g;

interface ChatPanelProps {
  apiKey: string;
}

export function ChatPanel({ apiKey }: ChatPanelProps) {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState("");
  const [uris, setUris] = useState<string[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(false);

  async function submit() {
    if (!query.trim()) return;
    setLoading(true);
    setResponse("");
    setUris([]);
    setDegraded(false);
    let accumulated = "";

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify({ query }),
      });

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) return;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value);
        for (const line of text.split("\n")) {
          if (line.startsWith("data: ")) {
            const chunk = line.slice(6);
            if (chunk === "[DONE]") break;
            if (chunk === "[DEGRADED_RERANKING]") {
              setDegraded(true);
              continue;
            }
            accumulated += chunk;
            setResponse(accumulated);
          }
        }
      }

      const foundUris: string[] = [
        ...(accumulated.match(AIRFLOW_URI_RE) ?? []),
        ...(accumulated.match(DBCHECK_URI_RE) ?? []),
      ];
      setUris(foundUris);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4 max-w-2xl mx-auto">
      {degraded && (
        <div className="bg-yellow-100 border border-yellow-400 text-yellow-800 px-4 py-2 rounded text-sm">
          Note: reranking service unavailable — results may be less precise.
        </div>
      )}
      <div className="flex gap-2">
        <input
          className="flex-1 border rounded px-3 py-2 text-sm"
          placeholder="Ask about pipeline or data quality..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <button
          className="bg-blue-600 text-white px-4 py-2 rounded text-sm disabled:opacity-50"
          onClick={submit}
          disabled={loading}
        >
          {loading ? "Sending…" : "Send"}
        </button>
      </div>
      {response && (
        <div className="bg-gray-50 border rounded p-4 text-sm whitespace-pre-wrap">
          {response}
        </div>
      )}
      {uris.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-semibold text-gray-500">Sources</span>
          {uris.map((uri) => (
            <SourceLink key={uri} uri={uri} />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Write `frontend/components/StatusTable.tsx`**

```typescript
// frontend/components/StatusTable.tsx
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
```

- [ ] **Step 5: Write `frontend/components/DagStatusList.tsx`**

```typescript
// frontend/components/DagStatusList.tsx
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
```

- [ ] **Step 6: Write `frontend/app/page.tsx`**

```typescript
// frontend/app/page.tsx
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
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd frontend && npx jest
```

Expected: PASS (7 tests)

- [ ] **Step 8: Commit**

```bash
git add frontend/
git commit -m "feat: Next.js frontend (Dashboard, ChatPanel SSE, degraded banner, SourceLink URI resolver)"
```
