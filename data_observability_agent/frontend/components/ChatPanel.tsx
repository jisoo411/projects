"use client";
import { useState } from "react";
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
