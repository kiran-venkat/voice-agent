"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { BACKEND_URL } from "../../lib/livekit";

interface CallSession {
  id: string;
  room_name: string;
  status: string;
  summary: string | null;
  started_at: string;
  ended_at: string | null;
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-green-100 text-green-700",
  ended: "bg-gray-200 text-gray-600",
};

function formatDuration(start: string, end: string | null): string {
  if (!end) return "ongoing";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  const secs = Math.round(ms / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function CallsPage() {
  const [calls, setCalls] = useState<CallSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/calls`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCalls((await res.json()) as CallSession[]);
      setError("");
    } catch {
      setError("Could not load calls. Is the backend running on port 8000?");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load + auto-refresh every 30s.
  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <main className="min-h-screen bg-gray-100 p-6">
      <div className="max-w-6xl mx-auto">
        <header className="mb-6">
          <h1 className="text-xl font-bold text-gray-900">Call History</h1>
          <p className="text-sm text-gray-500">
            Past calls and their post-call summaries · auto-refreshes every 30s
          </p>
        </header>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mb-4">
            {error}
          </p>
        )}

        {loading && calls.length === 0 ? (
          <p className="text-sm text-gray-400">Loading calls…</p>
        ) : calls.length === 0 ? (
          <div className="bg-white rounded-xl border p-10 text-center">
            <p className="text-sm text-gray-400">No calls yet</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-400 text-[11px] uppercase tracking-wide">
                <tr>
                  <th className="text-left font-semibold px-4 py-3">Room Name</th>
                  <th className="text-left font-semibold px-4 py-3">Started At</th>
                  <th className="text-left font-semibold px-4 py-3">Duration</th>
                  <th className="text-left font-semibold px-4 py-3">Status</th>
                  <th className="text-left font-semibold px-4 py-3">Summary</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((call) => {
                  const expanded = expandedId === call.id;
                  const badge = STATUS_STYLES[call.status] ?? "bg-gray-100 text-gray-600";
                  return (
                    <Fragment key={call.id}>
                      <tr
                        onClick={() =>
                          setExpandedId(expanded ? null : call.id)
                        }
                        className="border-t hover:bg-gray-50 cursor-pointer"
                      >
                        <td className="px-4 py-3 font-medium text-gray-800">
                          {call.room_name}
                        </td>
                        <td className="px-4 py-3 text-gray-600">
                          {formatTime(call.started_at)}
                        </td>
                        <td className="px-4 py-3 text-gray-600">
                          {formatDuration(call.started_at, call.ended_at)}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${badge}`}
                          >
                            {call.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-gray-500 max-w-xs truncate">
                          {call.summary
                            ? call.summary.split("\n")[0]
                            : "No summary"}
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="border-t bg-gray-50">
                          <td colSpan={5} className="px-4 py-4">
                            <p className="text-[11px] uppercase tracking-wide text-gray-400 font-semibold mb-2">
                              Post-Call Summary
                            </p>
                            <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                              {call.summary || "No summary available for this call."}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  );
}
