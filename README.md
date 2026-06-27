# Voice Agent — Conversational Booking with Live Monitoring & Warm Transfer

![CI](https://github.com/kiran-venkat/voice-agent/actions/workflows/ci.yml/badge.svg)

A real-time voice agent ("Alex") that books appointments over a natural phone-style
conversation, streams the entire call to a live monitoring dashboard, lets a human
watcher silently take over mid-call, and warm-transfers to a real human agent via
Twilio when the caller needs one.

## What it does

- **Books appointments by voice** — collects name, reason, date/time, and phone, checks
  availability, and confirms the booking via LLM tool calls (stored in PostgreSQL).
- **Manages existing appointments** — look up by phone, reschedule, or cancel, all by voice.
- **Streams the call live** — a Next.js dashboard shows the running transcript, the
  agent's state (listening / thinking / speaking), the detected intent, the action it's
  taking, the booking data collected so far, and **per-turn pipeline latency**
  (STT / LLM first-token / TTS first-byte) — all in real time over the LiveKit data channel.
- **Lets a watcher take over** — one click pauses the agent and hands the conversation
  to the human watcher, who speaks to the caller directly; another click hands it back.
- **Warm-transfers to a human** — when the caller asks for "a person" or wants to escalate
  a complaint/billing dispute, the agent dials a real human agent — into the call over
  LiveKit SIP, or via a Twilio summary + accept/decline fallback — then bridges them in or
  returns to the caller. (A routine "billing" *visit reason* does not trigger a transfer.)
- **Generates a post-call summary** — when the call ends, the LLM produces a concise
  recap (purpose, outcome, bookings, open issues), shown on the dashboard and persisted.
- **Browse history & bookings** — a **Call History** page lists past calls with their
  summaries, and a **Bookings** page lists every appointment; a top nav bar links all pages.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            LiveKit Cloud                                  │
│                                                                           │
│   ┌──────────┐   WebRTC audio   ┌──────────────────────────────────┐     │
│   │  Caller  │◄────────────────►│   Voice Agent (Agent A) — Python  │     │
│   │ (browser)│                  │   STT  Deepgram Nova-2            │     │
│   │  or SIP  │                  │   LLM  Groq llama-3.3-70b        │     │
│   └──────────┘                  │   TTS  Deepgram Aura (asteria)   │     │
│   ┌──────────┐  data channel    │   VAD  Silero + turn detector    │     │
│   │ Watcher  │◄─────────────────│                                  │     │
│   │(dashboard)│── TAKEOVER ────►│   tools: check_availability,     │     │
│   └──────────┘                  │   book/reschedule/cancel/lookup, │     │
│                                  │   request_human_transfer         │     │
│                                  └─────────────────┬────────────────┘     │
└──────────────────────────────────────────────────│──────────────────────┘
                                                    │ tool calls / REST
                          ┌─────────────────────────▼─────────────────────┐
                          │           FastAPI Backend (port 8000)          │
                          │  POST /api/token      issue LiveKit tokens     │
                          │  POST /api/webhook    LiveKit lifecycle hooks  │
                          │  GET  /api/appointments                        │
                          │  POST /api/twilio/*   TwiML for warm transfer  │
                          └──────────┬────────────────────────┬───────────┘
                                     │                         │
                          ┌──────────▼─────────┐     ┌─────────▼──────────┐
                          │  PostgreSQL        │     │  Twilio: SIP trunk │
                          │  appointments      │     │  or REST → human   │
                          │  call_sessions     │     │  agent's phone     │
                          └────────────────────┘     └────────────────────┘

      ┌──────────────────────────────────────────────────────────────┐
      │  Next.js Frontend — / (caller) · /monitor (watcher) ·          │
      │                     /calls (history) · /bookings (appointments)│
      └──────────────────────────────────────────────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for detailed data-flow diagrams.

## Prerequisites

| Requirement       | Version / Notes                                            |
|-------------------|------------------------------------------------------------|
| Node.js           | 20+                                                        |
| Python            | 3.11+                                                      |
| Docker            | For PostgreSQL (and optional full-stack compose)           |
| LiveKit account   | https://cloud.livekit.io — URL + API key/secret            |
| Deepgram account  | https://console.deepgram.com — STT + TTS API key           |
| Groq account      | https://console.groq.com — LLM API key (free, fastest)     |
| Twilio account    | https://console.twilio.com — for warm transfer (free trial)|

> OpenAI is optional — it's the LLM fallback if `GROQ_API_KEY` is unset.

## Setup

### a. Clone the repository

```bash
git clone <your-repo-url> voice-agent
cd voice-agent
```

### b. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your keys (see the [table below](#environment-variables) for
where to get each one).

### c. Start PostgreSQL

```bash
docker run -d --name voice-agent-db \
  -e POSTGRES_USER=voice_agent \
  -e POSTGRES_PASSWORD=voice_agent_dev \
  -e POSTGRES_DB=voice_agent \
  -p 5432:5432 \
  postgres:16-alpine
```

The backend creates its tables automatically on first start.

### d. Install backend dependencies

```bash
pip install -r backend/requirements.txt
```

> Tip: use a virtualenv — `python -m venv .venv && source .venv/bin/activate` first.

### e. Run the FastAPI server

```bash
PYTHONPATH=backend python backend/main.py
```

Serves on **http://localhost:8000** (health check: `GET /health`).

### f. Run the LiveKit agent worker

In a **second terminal**:

```bash
PYTHONPATH=backend python backend/agent.py start
```

This connects to LiveKit and waits for callers to join a room.

### g. Install frontend dependencies

```bash
cd frontend
npm install
```

### h. Run the frontend

```bash
npm run dev
```

Serves on **http://localhost:3001** (port 3000 is reserved for Docker — see note below).

| URL                              | Role                                          |
|----------------------------------|-----------------------------------------------|
| http://localhost:3001            | **Caller** — join the call, talk to Alex      |
| http://localhost:3001/monitor    | **Watcher** — live transcript + take-over     |
| http://localhost:3001/calls      | **Call History** — past calls + summaries     |
| http://localhost:3001/bookings   | **Bookings** — all appointments               |

> **Port note:** local dev uses **3001** because `docker-compose` maps the frontend to
> host port **3000**. If you run via compose instead, use **http://localhost:3000**.

## How each flow works

### a. Booking conversation

1. Caller joins the LiveKit room from `/`; the agent greets them.
2. Audio flows **VAD (Silero) → STT (Deepgram) → LLM (Groq) → TTS (Deepgram)**.
3. The LLM collects the five required fields one at a time: name, reason, date,
   time slot, phone.
4. The LLM calls **`check_availability(date, time_slot)`** — a tool that queries the
   `appointments` table for conflicts and returns alternatives if the slot is taken.
5. On confirmation, the LLM calls **`book_appointment(...)`**, which inserts the row and
   returns a confirmation number (`APT-XXXXXXXX`).
6. The agent reads the confirmation back to the caller.

The agent can also manage existing appointments: **`lookup_appointment(phone)`** reads
back a caller's upcoming bookings, **`reschedule_appointment(confirmation_number, new_date,
new_time)`** moves one (re-checking the new slot is free), and
**`cancel_appointment(confirmation_number)`** cancels one (idempotent).

### b. Live monitoring + take-over

1. Each call uses a **unique room** (`main-room-<timestamp>`) so every call is its own
   session. The watcher opens `/monitor` and clicks **Start Monitoring**; it finds the
   most recent **active** call via `GET /api/calls` and joins that room as a
   subscribe-only participant (no audio publish — enforced by the token grants in
   `main.py`). Start the call first, then monitor.
2. The agent publishes typed events on the LiveKit data channel (topic `agent-monitor`):
   `transcript`, `agent_state`, `intent`, `booking_update`, `call_status`,
   `transfer_status`, and `turn_metrics` (per-turn STT/LLM/TTS latency). The dashboard
   renders each in real time, including a live latency panel.
3. **Take Over** → the dashboard sends `{ "type": "TAKEOVER_REQUEST" }` on the data
   channel. The agent's `data_received` handler sets `_paused = True`, interrupts its
   current speech, and stops responding to STT input. Status flips to *Watcher in
   control*.
4. The watcher now speaks to the caller directly over WebRTC audio.
5. **Release Control** → sends `{ "type": "TAKEOVER_END" }`; the agent resumes.

### c. Warm transfer

1. Caller says something like *"I want to talk to a person about my bill."*
2. The LLM detects the intent and calls **`request_human_transfer(reason, caller_name)`**,
   and the agent publishes `transfer_status: initiated`.
3. `services/transfer.py:initiate_transfer()` picks a path:

   **Preferred — real audio bridge (LiveKit SIP).** If `LIVEKIT_SIP_TRUNK_ID` is set,
   `dial_human_into_room()` creates a SIP participant on the outbound trunk that dials the
   human's phone **into the same LiveKit room** as the caller. On answer, the AI pauses
   (`transfer_status: accepted`, `call_status: transferring`) and the human and caller talk
   directly. If the human doesn't answer, the AI resumes and apologises.

   **Fallback — Twilio REST + TwiML.** If no SIP trunk is configured, it dials via the
   Twilio REST API and the human hears a spoken summary + **press 1 to accept / 2 to
   decline** (`/api/twilio/transfer-answer` → `/api/twilio/transfer-keypress`). This rings
   the human and plays the summary but does **not** bridge the WebRTC caller's audio.

> Set up the SIP trunk with `backend/scripts/setup_sip_trunk.py` (writes
> `LIVEKIT_SIP_TRUNK_ID`). See the "Twilio + warm transfer" section of CLAUDE.md for the
> full setup and known gotchas (trial-account message, public webhook URL, etc.).

## Environment variables

| Variable                    | Description                                            | Where to get it                              |
|-----------------------------|--------------------------------------------------------|----------------------------------------------|
| `LIVEKIT_URL`               | LiveKit server WebSocket URL (`wss://…`)               | LiveKit Cloud → Project → Settings           |
| `LIVEKIT_API_KEY`           | LiveKit API key                                        | LiveKit Cloud → Settings → API Keys          |
| `LIVEKIT_API_SECRET`        | LiveKit API secret                                     | LiveKit Cloud → Settings → API Keys          |
| `GROQ_API_KEY`              | Groq LLM key (primary — fastest free tier)             | https://console.groq.com/keys                |
| `OPENAI_API_KEY`            | OpenAI key (fallback if Groq unset)                    | https://platform.openai.com/api-keys         |
| `LLM_MODEL`                 | Model name (default `llama-3.3-70b-versatile` — reliable tool-calling) | —                            |
| `LLM_BASE_URL`              | OpenAI-compatible base URL (Groq's by default)         | —                                            |
| `DEEPGRAM_API_KEY`          | Deepgram key for STT + TTS                             | https://console.deepgram.com → API Keys      |
| `DEEPGRAM_STT_MODEL`        | STT model (default `nova-2`)                           | —                                            |
| `DEEPGRAM_TTS_VOICE`        | TTS voice (default `aura-asteria-en`; aura-2 needs paid access) | —                                   |
| `DATABASE_URL`              | Async Postgres DSN (`postgresql+asyncpg://…`)          | matches the `docker run` command above        |
| `TWILIO_ACCOUNT_SID`        | Twilio account SID                                     | https://console.twilio.com                   |
| `TWILIO_AUTH_TOKEN`         | Twilio auth token                                      | https://console.twilio.com                   |
| `TWILIO_PHONE_NUMBER`       | Twilio number to dial **from** (E.164)                 | Twilio Console → Phone Numbers               |
| `TWILIO_HUMAN_AGENT_NUMBER` | Human agent number to dial **to** (E.164)              | your own / agent's phone                     |
| `LIVEKIT_SIP_TRUNK_ID`      | Outbound SIP trunk id (enables the real audio bridge)  | `backend/scripts/setup_sip_trunk.py`         |
| `TWILIO_SIP_TERMINATION_URI`| Twilio Elastic SIP trunk termination URI               | Twilio Console → Elastic SIP Trunking        |
| `TWILIO_SIP_USERNAME`       | SIP credential-list username                           | Twilio Console → SIP credentials             |
| `TWILIO_SIP_PASSWORD`       | SIP credential-list password                           | Twilio Console → SIP credentials             |
| `PUBLIC_BASE_URL`           | Public URL of the FastAPI app (for Twilio webhooks)    | ngrok URL in dev                             |
| `APP_ENV`                   | `development` or `production`                          | —                                            |
| `PORT`                      | FastAPI port (default `8000`)                          | —                                            |
| `LOG_LEVEL`                 | `DEBUG` / `INFO` / `WARNING` / `ERROR`                 | —                                            |

## Running tests

```bash
# Unit tests — pure functions (phone masking, confirmation parsing, TwiML). No services needed.
PYTHONPATH=backend python backend/tests/test_unit.py

# API tests — require the FastAPI backend running on :8000
PYTHONPATH=backend python backend/tests/test_api.py

# Booking tests — require PostgreSQL reachable (book / reschedule / cancel / lookup flow)
PYTHONPATH=backend python backend/tests/test_booking.py
```

The unit tests run anywhere (used in CI); the API and booking tests are integration
tests that need the backend and database up. See [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Run everything with Docker Compose

```bash
cp .env.example .env   # fill in keys first
docker compose up --build
```

This starts Postgres, the FastAPI backend (`:8000`), the agent worker, and the
frontend (`:3000`) together. See [docker-compose.yml](docker-compose.yml).

## Project layout

```
voice-agent/
├── backend/
│   ├── agent.py          LiveKit agent worker (VoiceAgent + @function_tool methods)
│   ├── main.py           FastAPI app (token, webhook, appointments, Twilio TwiML)
│   ├── config.py         Pydantic settings — all env vars
│   ├── database/         async SQLAlchemy models + session
│   ├── tools/            appointment tools (book / reschedule / cancel / lookup)
│   ├── services/         monitoring (data channel) + transfer (SIP / Twilio)
│   ├── scripts/          setup_sip_trunk.py, reset_room.py
│   └── tests/            test_unit · test_api · test_booking
├── frontend/
│   ├── app/page.tsx      caller call UI
│   ├── app/monitor/      watcher dashboard (transcript, state, latency, take-over)
│   ├── app/calls/        call history (past calls + summaries)
│   ├── app/bookings/     all appointments
│   ├── app/components/   shared UI (NavBar)
│   └── lib/livekit.ts    token + data-channel helpers
├── docs/architecture.md
├── .github/workflows/    ci.yml — frontend typecheck + backend unit tests
├── docker-compose.yml
├── .env.example
└── CLAUDE.md
```
