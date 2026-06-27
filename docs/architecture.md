# Architecture

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          LiveKit Cloud                                   │
│                                                                           │
│   ┌──────────┐   WebRTC audio   ┌──────────────────────────────────┐    │
│   │  Caller  │◄────────────────►│  Voice Agent (Agent A)           │    │
│   │ (Browser │                  │  ┌─────────────────────────────┐ │    │
│   │  or SIP) │                  │  │ STT: Deepgram Nova-2        │ │    │
│   └──────────┘                  │  │ LLM: Claude Haiku 4.5       │ │    │
│                                  │  │ TTS: Deepgram Aura (asteria)│ │    │
│   ┌──────────┐  data channel    │  │ VAD: Silero + turn detector │ │    │
│   │  Monitor │◄─────────────────│  └─────────────────────────────┘ │    │
│   │ (watcher)│─ TAKEOVER_REQ ──►│                                  │    │
│   └──────────┘                  └──────────────────────────────────┘    │
│                                            │                              │
└────────────────────────────────────────────│──────────────────────────────┘
                                             │ Tool calls
                          ┌──────────────────▼──────────────────┐
                          │       FastAPI Backend                 │
                          │                                       │
                          │  POST /api/token      (room tokens)  │
                          │  POST /api/webhook    (LiveKit hook)  │
                          │  GET  /api/appointments               │
                          │  GET  /api/calls      (call history)  │
                          │                                       │
                          └──────────┬──────────────┬────────────┘
                                     │              │
                          ┌──────────▼────┐  ┌──────▼──────────┐
                          │  PostgreSQL   │  │  Twilio REST API │
                          │ appointments  │  │  outbound call   │
                          │ call_sessions │  │  warm transfer   │
                          └───────────────┘  └─────────────────┘
```

## Component Responsibilities

### Voice Agent (`backend/agent.py`)

The LiveKit Agents worker process. One worker instance handles multiple
concurrent rooms. Each call room gets one `VoiceAgent` instance.

Pipeline per utterance:
```
Microphone audio → VAD (Silero) → STT (Deepgram) → LLM (Claude Haiku 4.5)
                                                       ↓
                                              Tool calls (if any)
                                                       ↓
                                              TTS (Deepgram) → Speaker
```

Side channel (data channel, parallel to audio):
```
Agent state change → publish_event("agent_state") → Monitor UI
User speech final → publish_event("transcript", role="user")
Agent speech done → publish_event("transcript", role="agent")
Tool called → publish_event("booking_update" / "transfer_status")
Per-turn metrics → publish_event("turn_metrics", {stt_ms, llm_ttft_ms, tts_ttfb_ms, eou_ms})
```

Latency metrics come from the SDK's `metrics_collected` event (LLMMetrics.ttft,
TTSMetrics.ttfb, EOUMetrics.transcription_delay) — see `_wire_session_events` in
`agent.py`. The dashboard merges the partial events into a live latency panel.

### FastAPI Backend (`backend/main.py`)

Serves token endpoint for browser clients, handles LiveKit webhooks for
call lifecycle events, and exposes REST API for the monitoring dashboard
to query appointments and call sessions.

Notes:
- **Per-call rooms:** each caller joins a unique room (`main-room-<timestamp>`), so each
  call is its own `call_sessions` row (the `/calls` page shows one row per call). The
  caller token embeds a `RoomConfiguration` agent dispatch + a short `empty_timeout`.
- **Webhooks** (`room_started`/`room_finished`) create/close the `call_sessions` row.
  Post-call, the agent also persists the LLM summary to that row (`_persist_call_summary`),
  so summaries survive even if the webhook didn't fire.
- **Takeover is over the data channel** (`TAKEOVER_REQUEST`/`TAKEOVER_END`), not a REST
  endpoint.
- Frontend pages: `/` caller · `/monitor` watcher · `/calls` history · `/bookings` appts.

### Tool Calls (`backend/tools/appointment.py`)

LLM calls these tools (defined as `@function_tool` methods on `VoiceAgent`, so they
also push `booking_update` events to the dashboard):

1. **`check_availability(date, time_slot)`** — queries `appointments` table
   for conflicts. Returns available/booked + nearest alternatives.

2. **`book_appointment(name, reason, date, time_slot, phone)`** — inserts row
   into `appointments`. Returns confirmation number (`APT-XXXXXXXX`).

3. **`lookup_appointment(phone)`** — returns the caller's upcoming, non-cancelled
   appointments, soonest first.

4. **`reschedule_appointment(confirmation_number, new_date, new_time)`** — moves an
   appointment after verifying the new slot is free.

5. **`cancel_appointment(confirmation_number)`** — marks an appointment cancelled
   (idempotent).

6. **`request_human_transfer(reason, caller_name)`** — signals warm transfer. Calls
   `services/transfer.py` which dials the human (SIP into the room, or Twilio fallback).

### Monitoring (`backend/services/monitoring.py`)

Helper that wraps `room.local_participant.publish_data()` with a typed
event schema. All components import `publish_event(room, event_type, data)`.

### Warm Transfer (`backend/services/transfer.py`)

`initiate_transfer()` chooses a path based on configuration:

**Preferred — LiveKit SIP audio bridge** (when `LIVEKIT_SIP_TRUNK_ID` is set):
1. `dial_human_into_room()` calls `api.CreateSIPParticipant` on the outbound trunk,
   dialing the human's phone **into the same LiveKit room** as the caller.
2. `wait_until_answered=True` blocks until a real pickup (not ringing).
3. On answer, the agent (via `participant_connected`) pauses the AI and the human and
   caller talk directly. Publishes `transfer_status: accepted` / `call_status: transferring`.
4. If the human never answers/declines, the AI resumes and apologises.

**Fallback — Twilio REST + TwiML** (no SIP trunk configured):
1. `_twilio_rest_transfer()` creates an outbound Twilio call to the human.
2. Twilio hits `/api/twilio/transfer-answer` → TwiML speaks the summary + `Gather`.
3. Press 1 → `/api/twilio/transfer-keypress` → conference TwiML; press 2 → decline.
4. This rings the human and plays the summary but does **not** bridge the WebRTC
   caller's audio (a Twilio conference can't reach a LiveKit participant).

## Data Flow: Appointment Booking

```
Caller: "I'd like to book an appointment"
  → STT: "I'd like to book an appointment"
  → LLM: recognises booking intent
  → Agent: "Sure! What's your name?"
  → publish_event(intent="booking")

Caller: "John Smith"
  → LLM: collects name
  → publish_event(booking_update, field="name", value="John Smith")

[... name, reason, date/time, phone collected ...]

  → LLM calls check_availability(date="2026-07-01", time_slot="10:00")
  → tool returns { available: true }
  → LLM calls book_appointment(name="John Smith", ...)
  → tool inserts DB row, returns { confirmation: "APT-001" }
  → Agent: "Perfect! I've booked your appointment for July 1st at 10 AM.
             Your confirmation number is APT-001."
  → publish_event(call_status, status="booking_confirmed")
```

## Data Flow: Watcher Takeover

```
Watcher clicks "Take Over" in dashboard
  → Frontend publishes to room data channel: { type: "TAKEOVER_REQUEST" }
  → Agent receives data_received event
  → Agent sets _paused = True, interrupts current speech
  → Agent publishes: { type: "TAKEOVER_GRANTED" }
  → Agent stops responding to STT input

Watcher speaks directly to caller via WebRTC audio (browser mic)

Watcher clicks "Hand Back"
  → Frontend publishes: { type: "TAKEOVER_END" }
  → Agent sets _paused = False
  → Agent: "I'm back. How can I continue helping you?"
```

## Data Flow: Warm Transfer

```
Caller: "I want to talk to a person about my bill"
  → LLM detects transfer intent
  → LLM calls request_human_transfer(reason="billing enquiry")
  → Agent: "I'm connecting you to a billing specialist. Please hold."
  → publish_event(transfer_status, status="initiated")

  → services/transfer.py calls Twilio REST:
      POST /2010-04-01/Accounts/{sid}/Calls
      { To: HUMAN_AGENT_NUMBER, url: PUBLIC_BASE_URL/api/twilio/transfer-answer }

  → Twilio dials human agent phone
  → Human answers → Twilio plays: "Transfer from Alex. Caller: billing enquiry.
                                    Press 1 to accept, 2 to decline."
  → Human presses 1
  → Twilio webhook → /api/twilio/transfer-accepted
  → Caller and human connected via Twilio conference
  → Agent disconnects from room
  → publish_event(transfer_status, status="accepted")

  → If human presses 2:
  → Agent resumes: "The billing team isn't available right now.
                     Can I help you with anything else?"
```

## Database Schema

```sql
CREATE TABLE appointments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  room_name    TEXT,                        -- LiveKit room where booked
  name         TEXT NOT NULL,
  reason       TEXT NOT NULL,
  date         DATE NOT NULL,
  time_slot    TEXT NOT NULL,              -- "10:00 AM"
  phone        TEXT NOT NULL,
  status       TEXT DEFAULT 'confirmed',   -- confirmed | cancelled | completed
  created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE call_sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  room_name    TEXT NOT NULL UNIQUE,
  status       TEXT DEFAULT 'active',      -- active | ended | transferred
  transcript   JSONB DEFAULT '[]',         -- [{role, text, ts}]
  collected    JSONB DEFAULT '{}',         -- booking data collected so far
  summary      TEXT,                       -- post-call LLM summary
  started_at   TIMESTAMPTZ DEFAULT now(),
  ended_at     TIMESTAMPTZ
);
```
