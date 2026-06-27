/**
 * LiveKit helpers — token fetching and data channel messaging.
 */

export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export const ROOM_NAME = "main-room";
export const MONITOR_TOPIC = "agent-monitor";

export type ParticipantRole = "caller" | "watcher";

export interface TokenResult {
  token: string;
  livekitUrl: string;
  roomName: string;
  identity: string;
}

/**
 * Fetch a LiveKit access token from the backend for the given role.
 * Throws if the backend is unreachable or returns a non-2xx status.
 */
export async function fetchToken(
  participantName: string,
  role: ParticipantRole,
  roomName: string = ROOM_NAME,
): Promise<TokenResult> {
  const res = await fetch(`${BACKEND_URL}/api/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      room_name: roomName,
      participant_name: participantName,
      role,
    }),
  });

  if (!res.ok) {
    throw new Error(`Token request failed: HTTP ${res.status}`);
  }

  const data = await res.json();
  return {
    token: data.token,
    livekitUrl: data.livekit_url,
    roomName: data.room_name,
    identity: data.identity,
  };
}

// ── Monitoring event types (mirror backend services/monitoring.py) ────────────

export type MonitorEventType =
  | "transcript"
  | "agent_state"
  | "intent"
  | "booking_update"
  | "call_status"
  | "transfer_status"
  | "turn_metrics";

export interface MonitorEvent {
  type: MonitorEventType;
  data: Record<string, unknown>;
  ts: string;
}

export interface TranscriptEntry {
  role: "user" | "agent";
  text: string;
  ts: string;
}

// ── Takeover control messages (sent watcher → agent) ──────────────────────────

export const TAKEOVER_REQUEST = { type: "TAKEOVER_REQUEST" };
export const TAKEOVER_END = { type: "TAKEOVER_END" };

/** Encode a JSON control message for publishing on the data channel. */
export function encodeControlMessage(message: object): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(message));
}

/** Decode a received data channel payload into a MonitorEvent, or null if malformed. */
export function decodeMonitorEvent(payload: Uint8Array): MonitorEvent | null {
  try {
    return JSON.parse(new TextDecoder().decode(payload)) as MonitorEvent;
  } catch {
    return null;
  }
}
