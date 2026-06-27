"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useDataChannel,
  useLocalParticipant,
} from "@livekit/components-react";
import {
  fetchToken,
  BACKEND_URL,
  MONITOR_TOPIC,
  decodeMonitorEvent,
  encodeControlMessage,
  TAKEOVER_REQUEST,
  TAKEOVER_END,
  type TranscriptEntry,
} from "../../lib/livekit";

// ── Badges ────────────────────────────────────────────────────────────────────

const STATE_STYLES: Record<string, { dot: string; label: string }> = {
  listening:    { dot: "bg-green-500", label: "Listening" },
  thinking:     { dot: "bg-yellow-400", label: "Thinking" },
  speaking:     { dot: "bg-blue-500", label: "Speaking" },
  initializing: { dot: "bg-gray-400", label: "Initializing" },
};

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  connected:       { bg: "bg-green-100", text: "text-green-700", label: "Connected" },
  takeover_active: { bg: "bg-purple-100", text: "text-purple-700", label: "Watcher in control" },
  transferring:    { bg: "bg-orange-100", text: "text-orange-700", label: "Transferring" },
  ended:           { bg: "bg-gray-200", text: "text-gray-600", label: "Call ended" },
};

const INTENT_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  booking:  { bg: "bg-blue-100", text: "text-blue-700", label: "Booking" },
  transfer: { bg: "bg-orange-100", text: "text-orange-700", label: "Wants human" },
  general:  { bg: "bg-gray-100", text: "text-gray-600", label: "General" },
};

// Canonical booking fields, in the order the agent collects them. `key` matches
// the field names published in booking_update events (see backend agent tools).
const BOOKING_FIELDS: { key: string; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "reason", label: "Reason" },
  { key: "date", label: "Date" },
  { key: "time_slot", label: "Time" },
  { key: "phone", label: "Phone" },
];

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border p-4">
      <p className="text-[11px] uppercase tracking-wide text-gray-400 font-semibold mb-2">
        {label}
      </p>
      {children}
    </div>
  );
}

// ── Dashboard interior (inside <LiveKitRoom>) ─────────────────────────────────

function MonitorDashboard() {
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [agentState, setAgentState] = useState("initializing");
  const [intent, setIntent] = useState("general");
  const [callStatus, setCallStatus] = useState("connected");
  const [transferStatus, setTransferStatus] = useState<string | null>(null);
  const [action, setAction] = useState("Waiting for caller…");
  const [collected, setCollected] = useState<Record<string, string>>({});
  const [summary, setSummary] = useState<string | null>(null);
  const [takeoverActive, setTakeoverActive] = useState(false);
  const [latency, setLatency] = useState<Record<string, number>>({});

  const bottomRef = useRef<HTMLDivElement>(null);
  const latencySamples = useRef<Record<string, number[]>>({});
  const prevCallStatus = useRef("connected");
  const { localParticipant } = useLocalParticipant();

  // Rolling average (last 50 samples) for a given latency metric, or null.
  const avgLatency = (key: string): number | null => {
    const arr = latencySamples.current[key] ?? [];
    return arr.length ? Math.round(arr.reduce((a, b) => a + b, 0) / arr.length) : null;
  };

  // Receive all monitoring events
  useDataChannel(MONITOR_TOPIC, (msg) => {
    const event = decodeMonitorEvent(msg.payload);
    if (!event) return;

    switch (event.type) {
      case "transcript":
        if (event.data.final !== false) {
          setTranscript((prev) => [
            ...prev,
            {
              role: event.data.role as "user" | "agent",
              text: event.data.text as string,
              ts: event.ts,
            },
          ]);
        }
        break;
      case "agent_state":
        setAgentState(event.data.state as string);
        break;
      case "intent":
        setIntent(event.data.intent as string);
        break;
      case "booking_update":
        setCollected((prev) => ({
          ...prev,
          [event.data.field as string]: event.data.value as string,
        }));
        setAction(`Collecting: ${event.data.field}`);
        break;
      case "call_status": {
        const status = event.data.status as string;
        // A new call starting (previous call had ended) → reset booking panel.
        if (status === "connected" && prevCallStatus.current === "ended") {
          setCollected({});
          setSummary(null);
          setTranscript([]);
        }
        prevCallStatus.current = status;
        setCallStatus(status);
        setTakeoverActive(status === "takeover_active");
        if (status === "ended") {
          setAction("Call ended");
          if (event.data.summary) setSummary(event.data.summary as string);
        }
        break;
      }
      case "transfer_status": {
        const status = event.data.status as string;
        setTransferStatus(status);
        if (status === "initiated") setAction("Transferring to human agent…");
        else if (status === "accepted") setAction("Human agent connected");
        else if (status === "declined") setAction("Human declined — agent resuming");
        break;
      }
      case "turn_metrics": {
        const d = event.data as Record<string, number>;
        setLatency((prev) => ({ ...prev, ...d }));
        for (const [k, v] of Object.entries(d)) {
          const arr = (latencySamples.current[k] ??= []);
          arr.push(v);
          if (arr.length > 50) arr.shift();
        }
        break;
      }
    }
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  // Map agent state → human-readable action when not overridden
  useEffect(() => {
    if (callStatus === "ended") return;
    if (agentState === "thinking") setAction("Thinking…");
    else if (agentState === "speaking") setAction("Speaking to caller");
    else if (agentState === "listening") setAction("Listening to caller");
  }, [agentState, callStatus]);

  const sendTakeover = useCallback(
    async (control: object) => {
      await localParticipant.publishData(
        encodeControlMessage(control),
        { reliable: true, topic: MONITOR_TOPIC },
      );
    },
    [localParticipant],
  );

  const handleTakeover = useCallback(() => {
    setTakeoverActive(true);
    void sendTakeover(TAKEOVER_REQUEST);
    // Enable the watcher's mic so they speak to the caller directly.
    void localParticipant.setMicrophoneEnabled(true);
  }, [sendTakeover, localParticipant]);

  const handleRelease = useCallback(() => {
    setTakeoverActive(false);
    void sendTakeover(TAKEOVER_END);
    // Mute the watcher again when handing control back to the agent.
    void localParticipant.setMicrophoneEnabled(false);
  }, [sendTakeover, localParticipant]);

  const status = STATUS_STYLES[callStatus] ?? STATUS_STYLES.connected;
  const stateStyle = STATE_STYLES[agentState] ?? STATE_STYLES.initializing;
  const intentStyle = INTENT_STYLES[intent] ?? INTENT_STYLES.general;
  const callEnded = callStatus === "ended";

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
      <RoomAudioRenderer />

      {/* ── Left: live state ─────────────────────────────────────────────── */}
      <div className="lg:col-span-1 flex flex-col gap-4">
        <Stat label="Call Status">
          <span className={`inline-flex px-3 py-1 rounded-full text-sm font-medium ${status.bg} ${status.text}`}>
            {status.label}
          </span>
        </Stat>

        <Stat label="Agent State">
          <div className="flex items-center gap-2">
            <span className={`w-3 h-3 rounded-full ${stateStyle.dot} ${!callEnded ? "animate-pulse" : ""}`} />
            <span className="text-sm font-medium text-gray-700">{stateStyle.label}</span>
          </div>
        </Stat>

        <Stat label="Detected Intent">
          <span className={`inline-flex px-3 py-1 rounded-full text-sm font-medium ${intentStyle.bg} ${intentStyle.text}`}>
            {intentStyle.label}
          </span>
        </Stat>

        <Stat label="Current Action">
          <p className="text-sm text-gray-700">{action}</p>
          {transferStatus && (
            <p className="text-xs text-orange-600 mt-1">Transfer: {transferStatus}</p>
          )}
        </Stat>

        <Stat label="Pipeline Latency (last · avg)">
          {Object.keys(latency).length === 0 ? (
            <p className="text-sm text-gray-400">No turns measured yet</p>
          ) : (
            <dl className="flex flex-col gap-1.5">
              {([
                ["eou_ms", "Response (EOU)"],
                ["stt_ms", "STT delay"],
                ["llm_ttft_ms", "LLM first token"],
                ["tts_ttfb_ms", "TTS first byte"],
              ] as const).map(([key, label]) => (
                latency[key] !== undefined && (
                  <div key={key} className="flex justify-between gap-2 text-sm">
                    <dt className="text-gray-400">{label}</dt>
                    <dd className="text-gray-800 font-medium tabular-nums">
                      {Math.round(latency[key])}
                      <span className="text-gray-400 font-normal"> · {avgLatency(key)} ms</span>
                    </dd>
                  </div>
                )
              ))}
            </dl>
          )}
        </Stat>

        <Stat label="Collecting Info">
          <dl className="flex flex-col gap-2">
            {(() => {
              // First not-yet-collected field is the one the agent is asking for.
              const active = callStatus !== "ended";
              const collectingKey = active
                ? BOOKING_FIELDS.find((f) => !collected[f.key])?.key
                : undefined;
              return BOOKING_FIELDS.map(({ key, label }) => {
                const value = collected[key];
                const state = value
                  ? "done"
                  : key === collectingKey
                  ? "collecting"
                  : "waiting";
                const icon =
                  state === "done" ? "✅" : state === "collecting" ? "⏳" : "⬜";
                const text =
                  state === "done"
                    ? value
                    : state === "collecting"
                    ? "collecting…"
                    : "waiting…";
                const textClass =
                  state === "done"
                    ? "text-gray-800 font-medium"
                    : state === "collecting"
                    ? "text-amber-600"
                    : "text-gray-400";
                return (
                  <div key={key} className="flex items-center gap-2 text-sm">
                    <span className="w-5 shrink-0 text-center">{icon}</span>
                    <dt className="w-16 shrink-0 text-gray-400">{label}</dt>
                    <dd className={`flex-1 text-right truncate ${textClass}`}>{text}</dd>
                  </div>
                );
              });
            })()}
          </dl>
        </Stat>
      </div>

      {/* ── Right: transcript + controls ─────────────────────────────────── */}
      <div className="lg:col-span-2 flex flex-col gap-4">
        {/* Takeover controls */}
        <div className="bg-white rounded-xl border p-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-gray-800">
              {takeoverActive ? "You are in control" : "Agent is handling the call"}
            </p>
            <p className="text-xs text-gray-400">
              {takeoverActive
                ? "The agent is paused. Speak to the caller directly."
                : "Take over to pause the agent and speak to the caller yourself."}
            </p>
          </div>
          {takeoverActive ? (
            <button
              onClick={handleRelease}
              disabled={callEnded}
              className="bg-gray-700 hover:bg-gray-800 disabled:opacity-40 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              Release Control
            </button>
          ) : (
            <button
              onClick={handleTakeover}
              disabled={callEnded}
              className="bg-purple-600 hover:bg-purple-700 disabled:opacity-40 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              Take Over
            </button>
          )}
        </div>

        {/* Transcript */}
        <div className="bg-white rounded-xl border flex flex-col h-[420px]">
          <div className="px-4 py-3 border-b flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-800">Live Transcript</h2>
            <span className="text-xs text-gray-400">{transcript.length} messages</span>
          </div>
          <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
            {transcript.length === 0 ? (
              <p className="text-sm text-gray-400 text-center mt-8">
                Waiting for conversation…
              </p>
            ) : (
              transcript.map((entry, i) => (
                <div key={i} className={`flex ${entry.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className="max-w-[75%]">
                    <p className={`text-[10px] uppercase tracking-wide mb-0.5 ${entry.role === "user" ? "text-right text-blue-400" : "text-gray-400"}`}>
                      {entry.role === "user" ? "Caller" : "Agent"}
                    </p>
                    <div
                      className={`px-3 py-2 rounded-2xl text-sm leading-relaxed ${
                        entry.role === "agent"
                          ? "bg-gray-100 text-gray-800 rounded-tl-sm"
                          : "bg-blue-600 text-white rounded-tr-sm"
                      }`}
                    >
                      {entry.text}
                    </div>
                  </div>
                </div>
              ))
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Post-call summary */}
        {summary && (
          <div className="bg-gradient-to-br from-indigo-50 to-white rounded-xl border border-indigo-200 p-5">
            <h2 className="text-sm font-semibold text-indigo-900 mb-2 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-indigo-500" />
              Post-Call Summary
            </h2>
            <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
              {summary}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function MonitorPage() {
  const [token, setToken] = useState("");
  const [lkUrl, setLkUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [watchedRoom, setWatchedRoom] = useState("");

  const connect = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // Rooms are unique per call now, so discover the most recent ACTIVE call
      // from the backend and watch that room.
      const res = await fetch(`${BACKEND_URL}/api/calls`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const calls = (await res.json()) as { room_name: string; status: string }[];
      const active = calls.find((c) => c.status === "active");
      if (!active) {
        setError("No active call to monitor right now. Start a call first, then monitor.");
        return;
      }
      const result = await fetchToken("Watcher", "watcher", active.room_name);
      setWatchedRoom(active.room_name);
      setToken(result.token);
      setLkUrl(result.livekitUrl);
    } catch {
      setError("Could not connect. Is the backend running on port 8000?");
    } finally {
      setLoading(false);
    }
  }, []);

  if (token && lkUrl) {
    return (
      <main className="min-h-screen bg-gray-100 p-6">
        <div className="max-w-6xl mx-auto">
          <header className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-xl font-bold text-gray-900">Monitoring Dashboard</h1>
              <p className="text-sm text-gray-500">Room: {watchedRoom} · Watching live</p>
            </div>
            <a href="/" className="text-sm text-gray-400 hover:text-gray-600 underline">
              ← Back to call
            </a>
          </header>

          <LiveKitRoom token={token} serverUrl={lkUrl} connect audio={false} video={false}>
            <MonitorDashboard />
          </LiveKitRoom>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-100 flex items-center justify-center p-6">
      <div className="bg-white rounded-2xl shadow-sm border p-8 w-full max-w-sm">
        <h1 className="text-2xl font-bold text-gray-900">Monitoring Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1 mb-6">
          Watch the live conversation, see the agent's state, and take over when needed.
        </p>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mb-4">
            {error}
          </p>
        )}

        <button
          onClick={connect}
          disabled={loading}
          className="w-full bg-gray-900 hover:bg-black disabled:opacity-50 text-white font-medium py-2.5 rounded-lg text-sm transition-colors"
        >
          {loading ? "Connecting…" : "Start Monitoring"}
        </button>

        <div className="mt-6 pt-4 border-t text-center">
          <a href="/" className="text-xs text-gray-400 hover:text-gray-600 underline">
            ← Back to call page
          </a>
        </div>
      </div>
    </main>
  );
}
