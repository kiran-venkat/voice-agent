"use client";

import { useCallback, useEffect, useState } from "react";
import { BACKEND_URL } from "../../lib/livekit";

interface Appointment {
  id: string;
  confirmation_number: string;
  name: string;
  reason: string;
  date: string;
  time_slot: string;
  phone: string;
  status: string;
  created_at: string;
}

const STATUS_STYLES: Record<string, string> = {
  confirmed: "bg-green-100 text-green-700",
  cancelled: "bg-red-100 text-red-600",
  completed: "bg-blue-100 text-blue-700",
};

export default function BookingsPage() {
  const [appts, setAppts] = useState<Appointment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/appointments`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAppts((await res.json()) as Appointment[]);
      setError("");
    } catch {
      setError("Could not load bookings. Is the backend running on port 8000?");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <main className="min-h-screen bg-gray-100 p-6">
      <div className="max-w-6xl mx-auto">
        <header className="mb-6">
          <h1 className="text-xl font-bold text-gray-900">Bookings</h1>
          <p className="text-sm text-gray-500">
            All appointments · auto-refreshes every 30s
          </p>
        </header>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mb-4">
            {error}
          </p>
        )}

        {loading && appts.length === 0 ? (
          <p className="text-sm text-gray-400">Loading bookings…</p>
        ) : appts.length === 0 ? (
          <div className="bg-white rounded-xl border p-10 text-center">
            <p className="text-sm text-gray-400">No bookings yet</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-400 text-[11px] uppercase tracking-wide">
                <tr>
                  <th className="text-left font-semibold px-4 py-3">Confirmation</th>
                  <th className="text-left font-semibold px-4 py-3">Name</th>
                  <th className="text-left font-semibold px-4 py-3">Reason</th>
                  <th className="text-left font-semibold px-4 py-3">Date</th>
                  <th className="text-left font-semibold px-4 py-3">Time</th>
                  <th className="text-left font-semibold px-4 py-3">Phone</th>
                  <th className="text-left font-semibold px-4 py-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {appts.map((a) => {
                  const badge = STATUS_STYLES[a.status] ?? "bg-gray-100 text-gray-600";
                  return (
                    <tr key={a.id} className="border-t hover:bg-gray-50">
                      <td className="px-4 py-3 font-mono text-xs text-gray-700">
                        {a.confirmation_number}
                      </td>
                      <td className="px-4 py-3 font-medium text-gray-800">{a.name}</td>
                      <td className="px-4 py-3 text-gray-600">{a.reason}</td>
                      <td className="px-4 py-3 text-gray-600">{a.date}</td>
                      <td className="px-4 py-3 text-gray-600">{a.time_slot}</td>
                      <td className="px-4 py-3 text-gray-500">{a.phone}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${badge}`}
                        >
                          {a.status}
                        </span>
                      </td>
                    </tr>
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
