"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useDataChannel,
} from "@livekit/components-react";
import {
  fetchToken,
  ROOM_NAME,
  MONITOR_TOPIC,
  decodeMonitorEvent,
  type TranscriptEntry,
} from "../lib/livekit";

// ── State badge ───────────────────────────────────────────────────────────────

const STATE_STYLES: Record<string, { dot: string; label: string }> = {
  listening:    { dot: "bg-green-500", label: "Listening" },
  thinking:     { dot: "bg-yellow-400", label: "Thinking…" },
  speaking:     { dot: "bg-blue-500", label: "Speaking" },
  initializing: { dot: "bg-gray-400", label: "Connecting…" },
  connecting:   { dot: "bg-gray-400", label: "Connecting…" },
};

function AgentStateBadge({ state }: { state: string }) {
  const style = STATE_STYLES[state] ?? { dot: "bg-gray-400", label: state };
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-white border rounded-full shadow-sm w-fit">
      <span className={`w-2.5 h-2.5 rounded-full ${style.dot} animate-pulse`} />
      <span className="text-xs font-medium text-gray-600">{style.label}</span>
    </div>
  );
}

// ── Call UI (must live inside <LiveKitRoom>) ──────────────────────────────────

function CallInterface({ participantName }: { participantName: string }) {
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [agentState, setAgentState] = useState("connecting");
  const bottomRef = useRef<HTMLDivElement>(null);

  // Subscribe to monitoring events from the agent's data channel
  useDataChannel(MONITOR_TOPIC, (msg) => {
    const event = decodeMonitorEvent(msg.payload);
    if (!event) return;

    if (event.type === "transcript" && event.data.final !== false) {
      setTranscript((prev) => [
        ...prev,
        {
          role: event.data.role as "user" | "agent",
          text: event.data.text as string,
          ts: event.ts,
        },
      ]);
    } else if (event.type === "agent_state") {
      setAgentState(event.data.state as string);
    }
  });

  // Auto-scroll transcript to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  return (
    <div className="flex flex-col gap-5">
      {/* Audio renderer — plays agent and other participant audio */}
      <RoomAudioRenderer />

      {/* Agent state */}
      <div className="flex items-center justify-between">
        <AgentStateBadge state={agentState} />
        <span className="text-xs text-gray-400">Speaking to Alex (AI Receptionist)</span>
      </div>

      {/* Mic indicator */}
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
        Microphone active — speak naturally
      </div>

      {/* Transcript */}
      <div className="border rounded-xl bg-gray-50 p-4 h-72 overflow-y-auto flex flex-col gap-3">
        {transcript.length === 0 ? (
          <p className="text-sm text-gray-400 text-center mt-8">
            Transcript will appear as you speak…
          </p>
        ) : (
          transcript.map((entry, i) => (
            <div
              key={i}
              className={`flex gap-2 ${entry.role === "user" ? "flex-row-reverse" : "flex-row"}`}
            >
              <div className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold mt-1
                              bg-gray-200 text-gray-600">
                {entry.role === "agent" ? "AI" : participantName[0].toUpperCase()}
              </div>
              <div
                className={`px-3 py-2 rounded-2xl text-sm max-w-[75%] leading-relaxed ${
                  entry.role === "agent"
                    ? "bg-white border text-gray-800 rounded-tl-sm"
                    : "bg-blue-600 text-white rounded-tr-sm"
                }`}
              >
                {entry.text}
              </div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <p className="text-xs text-gray-400 text-center">
        Say "book an appointment", ask about availability, or "talk to a person" for a human agent.
      </p>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function CallPage() {
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [lkUrl, setLkUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const joinCall = useCallback(async () => {
    if (!name.trim()) return;
    setLoading(true);
    setError("");
    try {
      const result = await fetchToken(name.trim(), "caller", ROOM_NAME);
      setToken(result.token);
      setLkUrl(result.livekitUrl);
    } catch {
      setError("Could not connect. Is the backend running on port 8000?");
    } finally {
      setLoading(false);
    }
  }, [name]);

  const leaveCall = useCallback(() => {
    setToken("");
    setLkUrl("");
  }, []);

  // ── Connected view ──────────────────────────────────────────────────────────
  if (token && lkUrl) {
    return (
      <main className="min-h-screen bg-gradient-to-br from-blue-50 to-gray-100 p-6">
        <div className="max-w-lg mx-auto">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-xl font-bold text-gray-900">Voice Agent</h1>
              <p className="text-sm text-gray-500">Joined as {name}</p>
            </div>
            <button
              onClick={leaveCall}
              className="text-sm text-red-500 border border-red-200 px-3 py-1.5 rounded-lg hover:bg-red-50 transition-colors"
            >
              Leave Call
            </button>
          </div>

          <div className="bg-white rounded-2xl shadow-sm border p-6">
            <LiveKitRoom
              token={token}
              serverUrl={lkUrl}
              connect
              audio
              video={false}
              onDisconnected={leaveCall}
            >
              <CallInterface participantName={name} />
            </LiveKitRoom>
          </div>

          <p className="text-xs text-gray-400 text-center mt-4">
            <a href="/monitor" className="underline hover:text-gray-600">
              Open monitoring dashboard →
            </a>
          </p>
        </div>
      </main>
    );
  }

  // ── Join view ───────────────────────────────────────────────────────────────
  return (
    <main className="min-h-screen bg-gradient-to-br from-blue-50 to-gray-100 flex items-center justify-center p-6">
      <div className="bg-white rounded-2xl shadow-sm border p-8 w-full max-w-sm">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Voice Agent</h1>
          <p className="text-sm text-gray-500 mt-1">
            Book an appointment with our AI receptionist, Alex.
          </p>
        </div>

        <div className="flex flex-col gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Your name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && joinCall()}
              placeholder="e.g. John Smith"
              className="w-full border border-gray-300 rounded-lg px-3 py-2.5 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              autoFocus
            />
          </div>

          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </p>
          )}

          <button
            onClick={joinCall}
            disabled={!name.trim() || loading}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed
                       text-white font-medium py-2.5 rounded-lg text-sm transition-colors"
          >
            {loading ? "Connecting…" : "Start Call"}
          </button>
        </div>

        <div className="mt-6 pt-4 border-t text-center">
          <a
            href="/monitor"
            className="text-xs text-gray-400 hover:text-gray-600 underline"
          >
            Open monitoring dashboard →
          </a>
        </div>
      </div>
    </main>
  );
}
