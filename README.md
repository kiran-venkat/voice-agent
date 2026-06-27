# Voice Agent — Conversational Booking with Live Monitoring & Warm Transfer

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
- **Warm-transfers to a human** — when the caller asks for billing, complaints, or "a
  person", the agent dials a real human agent via Twilio, speaks a summary, and bridges
  or declines based on the human's key-press.
- **Generates a post-call summary** — when the call ends, the LLM produces a concise
  recap (purpose, outcome, bookings, open issues) shown on the dashboard.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            LiveKit Cloud                                  │
│                                                                           │
│   ┌──────────┐   WebRTC audio   ┌──────────────────────────────────┐     │
│   │  Caller  │◄────────────────►│   Voice Agent (Agent A) — Python  │     │
│   │ (browser)│                  │   STT  Deepgram Nova-2            │     │
│   │  or SIP  │                  │   LLM  Groq llama-3.1-8b-instant │     │
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
                          │  PostgreSQL        │     │  Twilio REST API   │
                          │  appointments      │     │  outbound call →   │
                          │  call_sessions     │     │  human agent phone │
                          └────────────────────┘     └────────────────────┘

      ┌──────────────────────────────────────────────────────────────┐
      │  Next.js Frontend  —  /  (caller)   ·   /monitor  (watcher)    │
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

1. The watcher opens `/monitor` and joins the **same room** as a subscribe-only
   participant (no audio publish — enforced by the token grants in `main.py`).
2. The agent publishes typed events on the LiveKit data channel (topic `agent-monitor`):
   `transcript`, `agent_state`, `intent`, `booking_update`, `call_status`,
   `transfer_status`. The dashboard renders each in real time.
3. **Take Over** → the dashboard sends `{ "type": "TAKEOVER_REQUEST" }` on the data
   channel. The agent's `data_received` handler sets `_paused = True`, interrupts its
   current speech, and stops responding to STT input. Status flips to *Watcher in
   control*.
4. The watcher now speaks to the caller directly over WebRTC audio.
5. **Release Control** → sends `{ "type": "TAKEOVER_END" }`; the agent resumes.

### c. Warm transfer (Twilio)

1. Caller says something like *"I want to talk to a person about my bill."*
2. The LLM detects the intent and calls **`request_human_transfer(reason, caller_name)`**.
3. The agent tells the caller it's connecting them and publishes
   `transfer_status: initiated`.
4. `services/transfer.py` calls the Twilio REST API to dial
   `TWILIO_HUMAN_AGENT_NUMBER` from `TWILIO_PHONE_NUMBER`, pointing the call at the
   `/api/twilio/transfer-answer` TwiML webhook.
5. When the human picks up, the TwiML **speaks a summary** of the call and asks them to
   **press 1 to accept or 2 to decline**.
6. **Accept (1)** → `/api/twilio/transfer-keypress` returns conference TwiML; the human
   joins the bridge and the agent exits, leaving caller + human connected.
   **Decline (2)** → the human hears a goodbye; the agent resumes and tells the caller
   the team isn't available right now.

> Full caller-side audio bridging from the LiveKit room into the Twilio conference
> requires a LiveKit SIP trunk (see `add_caller_to_conference` in `services/transfer.py`).
> The transfer **initiation, summary, and accept/decline logic** are fully implemented.

## Environment variables

| Variable                    | Description                                            | Where to get it                              |
|-----------------------------|--------------------------------------------------------|----------------------------------------------|
| `LIVEKIT_URL`               | LiveKit server WebSocket URL (`wss://…`)               | LiveKit Cloud → Project → Settings           |
| `LIVEKIT_API_KEY`           | LiveKit API key                                        | LiveKit Cloud → Settings → API Keys          |
| `LIVEKIT_API_SECRET`        | LiveKit API secret                                     | LiveKit Cloud → Settings → API Keys          |
| `GROQ_API_KEY`              | Groq LLM key (primary — fastest free tier)             | https://console.groq.com/keys                |
| `OPENAI_API_KEY`            | OpenAI key (fallback if Groq unset)                    | https://platform.openai.com/api-keys         |
| `LLM_MODEL`                 | Model name (default `llama-3.3-70b-versatile`)         | —                                            |
| `LLM_BASE_URL`              | OpenAI-compatible base URL (Groq's by default)         | —                                            |
| `DEEPGRAM_API_KEY`          | Deepgram key for STT + TTS                             | https://console.deepgram.com → API Keys      |
| `DEEPGRAM_STT_MODEL`        | STT model (default `nova-2`)                           | —                                            |
| `DEEPGRAM_TTS_VOICE`        | TTS voice (default `aura-2-en-us`)                     | —                                            |
| `DATABASE_URL`              | Async Postgres DSN (`postgresql+asyncpg://…`)          | matches the `docker run` command above        |
| `TWILIO_ACCOUNT_SID`        | Twilio account SID                                     | https://console.twilio.com                   |
| `TWILIO_AUTH_TOKEN`         | Twilio auth token                                      | https://console.twilio.com                   |
| `TWILIO_PHONE_NUMBER`       | Twilio number to dial **from** (E.164)                 | Twilio Console → Phone Numbers               |
| `TWILIO_HUMAN_AGENT_NUMBER` | Human agent number to dial **to** (E.164)              | your own / agent's phone                     |
| `PUBLIC_BASE_URL`           | Public URL of the FastAPI app (for Twilio webhooks)    | ngrok URL in dev                             |
| `APP_ENV`                   | `development` or `production`                          | —                                            |
| `PORT`                      | FastAPI port (default `8000`)                          | —                                            |
| `LOG_LEVEL`                 | `DEBUG` / `INFO` / `WARNING` / `ERROR`                 | —                                            |

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
│   ├── agent.py          LiveKit agent worker (VoiceAgent + tools)
│   ├── main.py           FastAPI app (token, webhook, appointments, Twilio TwiML)
│   ├── config.py         Pydantic settings — all env vars
│   ├── database/         async SQLAlchemy models + session
│   ├── tools/            appointment tool implementations
│   └── services/         monitoring (data channel) + transfer (Twilio)
├── frontend/
│   ├── app/page.tsx      caller call UI
│   ├── app/monitor/      watcher dashboard
│   └── lib/livekit.ts    token + data-channel helpers
├── docs/architecture.md
├── docker-compose.yml
└── CLAUDE.md
```
