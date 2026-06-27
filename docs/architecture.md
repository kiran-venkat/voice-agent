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
│   └──────────┘                  │  │ LLM: Groq llama-3.3-70b     │ │    │
│                                  │  │ TTS: Deepgram Aura-2        │ │    │
│   ┌──────────┐  data channel    │  │ VAD: Silero                 │ │    │
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
                          │  POST /api/calls/:room/takeover       │
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
Microphone audio → VAD (Silero) → STT (Deepgram) → LLM (Groq)
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
```

### FastAPI Backend (`backend/main.py`)

Serves token endpoint for browser clients, handles LiveKit webhooks for
call lifecycle events, and exposes REST API for the monitoring dashboard
to query appointments and call sessions.

### Tool Calls (`backend/tools/appointment.py`)

LLM calls three tools:

1. **`check_availability(date, time_slot)`** — queries `appointments` table
   for conflicts. Returns available/booked + nearest alternatives.

2. **`book_appointment(name, reason, date, time_slot, phone)`** — inserts row
   into `appointments`. Returns confirmation number.

3. **`request_human_transfer(reason)`** — signals warm transfer. Calls
   `services/transfer.py` which dials Twilio, plays summary, bridges call.

### Monitoring (`backend/services/monitoring.py`)

Helper that wraps `room.local_participant.publish_data()` with a typed
event schema. All components import `publish_event(room, event_type, data)`.

### Warm Transfer (`backend/services/transfer.py`)

1. `initiate_transfer(room_name, caller_summary, human_agent_number)` — calls
   Twilio REST API to create an outbound call to the human agent.
2. Twilio hits `/api/twilio/transfer-answer` TwiML webhook.
3. TwiML plays summary, waits for key press (accept=1, decline=2).
4. If accepted: bridges via Twilio conference or SIP to LiveKit room.
5. If declined: Agent resumes conversation with caller.

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
