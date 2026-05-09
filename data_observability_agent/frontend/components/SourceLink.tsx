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
