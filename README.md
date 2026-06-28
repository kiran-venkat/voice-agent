# 🎙️ Voice Agent — Conversational Booking · Live Monitoring · Warm Transfer

[![CI](https://github.com/kiran-venkat/voice-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/kiran-venkat/voice-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-000000?logo=nextdotjs&logoColor=white)
![LiveKit](https://img.shields.io/badge/LiveKit-Agents%201.6-00C2FF)
![Claude](https://img.shields.io/badge/LLM-Claude%20Haiku%204.5-D97757)
![Deepgram](https://img.shields.io/badge/Speech-Deepgram-13EF93)
![Twilio](https://img.shields.io/badge/Telephony-Twilio%20SIP-F22F46?logo=twilio&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/DB-PostgreSQL%20async-4169E1?logo=postgresql&logoColor=white)

A real-time **voice AI receptionist** ("Alex"). A caller talks to it over WebRTC; it **books
appointments by voice** through LLM tool calls, a **live dashboard** streams the whole call to a
human supervisor who can **take over**, and when the caller needs a person it does a **warm
transfer to a real phone** with genuine two-way audio. Every call ends with an **LLM-generated
summary**. Built on **LiveKit + Claude + Deepgram + Twilio**, backend in **Python**, frontend in
**Next.js**.

## 🎥 Demo

**▶︎ [Watch the 5-minute walkthrough on Loom](https://www.loom.com/share/b890f6ed4b2244839359e0a9c529a123)**

Booking conversation → live monitoring → human take-over → warm transfer (summary + accept &
decline) → post-call summary.

---

## ✨ What makes this stand out

This isn't a "happy-path" prototype — it's engineered like a product, with the failure modes that
actually bite real voice agents already handled:

- 🎯 **Reliable tool-calling, not hallucinated bookings.** The agent's bookings are *real* DB
  writes via structured tool calls.
- 🗣️ **Natural turn-taking.** Silero VAD (acoustic) is paired with an **ONNX semantic
  end-of-turn model** (`livekit-plugins-turn-detector`) so Alex doesn't cut you off mid-thought
  when you pause.
- 📊 **Real-time observability built in.** The dashboard streams **per-turn pipeline latency** —
  STT delay, LLM time-to-first-token, TTS time-to-first-byte — so you can *see* the speech loop
  performing live, not just read a transcript.
- ☎️ **A *real* warm transfer.** Most demos drop the human into a Twilio conference the WebRTC
  caller can't reach. Here the human is dialed **into the LiveKit room over SIP** — genuine
  two-way audio — with a spoken handoff summary, and graceful **accept / decline** handling.
- 🧱 **Truthful agent.** The prompt + tool contract guarantee Alex **never fabricates** a
  confirmation: if a slot is taken or `book_appointment` fails, it says so and offers
  alternatives — it only confirms with the exact number the tool returned.
- 🧩 **Deterministic & isolated sessions.** Explicit agent dispatch + a **unique room per call**
  mean the agent reliably joins, and every call is its own auditable session (own history row,
  own summary) instead of all calls colliding in one shared room.
- 🛟 **Graceful degradation.** LLM falls back Claude → Groq → OpenAI; warm transfer falls back
  SIP bridge → Twilio REST IVR. Health checks, typed config, async DB throughout.
- ➕ **Beyond the brief:** reschedule / cancel / look-up tools, a live "collecting info" panel,
  a Bookings page, a Call History page with persisted summaries, and CI.

---

## 🗺️ Requirements coverage

| Requirement | Status | Where |
|---|:--:|---|
| Caller ↔ agent via a LiveKit room | ✅ | `agent.py`, `app/page.tsx` |
| Natural booking conversation (name, reason, date/time, phone) | ✅ | `SYSTEM_PROMPT`, tools |
| **Checks availability** via tool/function call before confirming | ✅ | `check_availability` |
| **Books** via tool call + reads back confirmation (stored in DB) | ✅ | `book_appointment` → PostgreSQL |
| Live transcript | ✅ | `/monitor` |
| Agent state (listening/thinking/speaking) + intent + current action | ✅ | data-channel events |
| Call status (connected → transferring → ended) | ✅ | `/monitor` |
| **Take over** from the UI (agent pauses) | ✅ | `TAKEOVER_REQUEST` / `_paused` |
| **Warm transfer** — summary + accept (connect) + decline (unavailable) | ✅ | LiveKit SIP (`transfer.py`) |
| **Post-call summary** | ✅ | `_generate_summary` → dashboard + DB |
| Next.js UI: call + monitoring + take-over | ✅ | `frontend/` |
| **Bonus** — reschedule / cancel / look-up | ✅ | `reschedule/cancel/lookup_appointment` |
| **Bonus** — collected data shown live | ✅ | "Collecting Info" panel |
| **Bonus** — semantic turn detection + per-turn latency | ✅ | turn-detector + `turn_metrics` |
| **Bonus** — Call History & Bookings pages, CI | ✅ | `/calls`, `/bookings`, GitHub Actions |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            LiveKit Cloud                                  │
│   ┌──────────┐   WebRTC audio   ┌──────────────────────────────────┐     │
│   │  Caller  │◄────────────────►│   Voice Agent (Agent A) — Python  │     │
│   │ (browser)│                  │   STT  Deepgram Nova-2            │     │
│   │  or SIP  │                  │   LLM  Claude Haiku 4.5          │     │
│   └──────────┘                  │   TTS  Deepgram Aura (asteria)   │     │
│   ┌──────────┐  data channel    │   VAD  Silero + ONNX turn model  │     │
│   │ Watcher  │◄─────────────────│   tools: check_availability,     │     │
│   │(dashboard)│── TAKEOVER ────►│   book/reschedule/cancel/lookup, │     │
│   └──────────┘                  │   request_human_transfer         │     │
│                                  └─────────────────┬────────────────┘     │
└──────────────────────────────────────────────────│──────────────────────┘
                                                    │ tool calls / REST
                          ┌─────────────────────────▼─────────────────────┐
                          │           FastAPI Backend (port 8000)          │
                          │  POST /api/token      issue LiveKit tokens     │
                          │  POST /api/webhook    LiveKit lifecycle hooks  │
                          │  GET  /api/appointments · /api/calls           │
                          │  POST /api/twilio/*   TwiML for warm transfer  │
                          └──────────┬────────────────────────┬───────────┘
                                     │                         │
                          ┌──────────▼─────────┐     ┌─────────▼──────────┐
                          │  PostgreSQL        │     │  Twilio: SIP trunk │
                          │  appointments      │     │  → human agent's   │
                          │  call_sessions     │     │  phone (PSTN)      │
                          └────────────────────┘     └────────────────────┘

   Next.js frontend — / (caller) · /monitor (watcher) · /calls (history) · /bookings
```

Detailed data-flow diagrams: [docs/architecture.md](docs/architecture.md). Engineering decision
log & operational notes: [CLAUDE.md](CLAUDE.md). Demo recording script: [docs/demo-walkthrough.md](docs/demo-walkthrough.md).

---

## 🧠 Engineering decisions (the interesting bits)

| Decision | Why |
|---|---|
| **Claude Haiku 4.5** as the LLM (via `livekit-plugins-anthropic`) | Llama on Groq faked tool calls and mis-reasoned dates; Haiku gives reliable structured tool-calling + low latency for voice. Falls back to Groq/OpenAI if no Anthropic key. |
| **Semantic turn detection** on top of Silero VAD | VAD only hears silence; the ONNX model reads the transcript to decide if the caller actually finished — far fewer mid-sentence interruptions. |
| **Explicit agent dispatch + unique room per call** | Auto-dispatch was flaky (callers stuck on "connecting"); a `RoomConfiguration` dispatch baked into each token + per-call room names make join deterministic and every call its own session. |
| **Warm transfer over LiveKit SIP** (not a Twilio conference) | A Twilio conference can't bridge a WebRTC caller. Dialing the human *into the LiveKit room* gives true two-way audio; Twilio REST + IVR remains as a no-SIP fallback. |
| **Tool-faithful prompt contract** | The agent must relay `available=false` / `success=false` and only confirm with the tool's real `confirmation_number` — eliminating "confirmed!" hallucinations. |
| **Async tail for monitoring** | The agent never blocks the speech loop on bookkeeping — state/intent/latency events are published to the LiveKit data channel and rendered live. |
| **Per-turn latency metrics** | Captured from the SDK's `metrics_collected` (LLM TTFT, TTS TTFB, STT/EOU delay) and surfaced on the dashboard for real observability. |

---

## ⚡ Quickstart

**Prerequisites:** Node 20+, Python 3.11+, Docker (for Postgres), and accounts/keys for
[LiveKit](https://cloud.livekit.io), [Deepgram](https://console.deepgram.com),
[Anthropic](https://console.anthropic.com), and (for warm transfer) [Twilio](https://console.twilio.com).

```bash
# 1. Configure
cp .env.example .env          # fill in your keys (see table below)

# 2. Database
docker run -d --name voice-agent-db \
  -e POSTGRES_USER=voice_agent -e POSTGRES_PASSWORD=voice_agent_dev \
  -e POSTGRES_DB=voice_agent -p 5432:5432 postgres:16-alpine
#   (tables are created automatically on first backend start)

# 3. Backend deps
pip install -r backend/requirements.txt   # ideally in a venv
```

Then run the **three processes** in separate terminals:

```bash
# Terminal 1 — FastAPI (tokens + webhooks)        → http://localhost:8000  (GET /health)
PYTHONPATH=backend python backend/main.py

# Terminal 2 — LiveKit agent worker               → waits for callers
PYTHONPATH=backend python backend/agent.py start

# Terminal 3 — Next.js frontend                   → http://localhost:3001
cd frontend && npm install && npm run dev
```

| URL | Role |
|-----|------|
| `http://localhost:3001` | **Caller** — join the call, talk to Alex |
| `http://localhost:3001/monitor` | **Watcher** — live transcript, state, take-over |
| `http://localhost:3001/calls` | **Call History** — past calls + summaries |
| `http://localhost:3001/bookings` | **Bookings** — all appointments |

> **Port note:** dev uses **3001** because `docker-compose` maps the frontend to host **3000**.
> Or run the whole stack with `docker compose up --build` and use **3000**.

> **First run only:** download the turn-detector weights once with
> `PYTHONPATH=backend python backend/agent.py download-files`.

---

## 🔁 How each flow works

### Booking
1. Caller joins the LiveKit room from `/`; Alex greets them.
2. Audio flows **Silero VAD → Deepgram STT → Claude → Deepgram TTS**, with the ONNX turn model
   deciding when the caller is really done.
3. Alex collects the 5 fields one at a time, calls **`check_availability`** (returns alternatives
   if the slot's taken), then **`book_appointment`** (inserts the row, returns `APT-XXXXXXXX`),
   and reads the confirmation back — digit by digit.
4. Existing appointments: **`lookup_appointment(phone)`**, **`reschedule_appointment(...)`**
   (re-checks the slot), **`cancel_appointment(...)`** (idempotent).

### Live monitoring + take-over
1. Each call uses a **unique room**; the watcher opens `/monitor` → **Start Monitoring**, which
   finds the active call via `GET /api/calls` and joins it (subscribe-only).
2. The agent publishes typed events on the data channel (`transcript`, `agent_state`, `intent`,
   `booking_update`, `call_status`, `transfer_status`, `turn_metrics`) — rendered live.
3. **Take Over** sends `TAKEOVER_REQUEST`; the agent pauses (interrupts its speech, stops
   replying). **Release Control** resumes it.

> ℹ️ The monitor is **visual-only by default** (audio playback disabled) so it can run beside the
> caller on one machine without echo. The watcher→caller **audio bridge is implemented** but
> commented off for that reason — re-enable it (and use headphones / a 2nd device) by uncommenting
> the `RoomAudioRenderer` + `setMicrophoneEnabled` lines in `app/monitor/page.tsx`.

### Warm transfer
1. Caller asks for a person → Alex calls **`request_human_transfer`** → `transfer_status: initiated`.
2. **LiveKit SIP (primary, real audio):** `dial_human_into_room()` dials the human's phone **into
   the room**. On **answer**, Alex speaks a handoff summary and goes silent → caller & human talk
   two-way (`accepted` / `transferring`). On **reject/no-answer**, Alex tells the caller *"the team
   isn't available right now"* and resumes.
3. **Twilio REST (fallback, no SIP trunk):** dials via REST + TwiML, plays a spoken summary, and
   offers **press 1 to accept / 2 to decline** (rings the human but doesn't bridge WebRTC audio).

> Set up the SIP trunk with `backend/scripts/setup_sip_trunk.py`. Full setup + Twilio-trial
> gotchas are in [CLAUDE.md](CLAUDE.md) → "Twilio + warm transfer".

### Post-call summary
When the call ends, the agent generates an LLM recap (purpose, outcome, bookings, open issues),
shows it on the dashboard, and **persists** it — visible on the **Call History** page.

---

## ⚙️ Environment variables

| Variable | Description | Where to get it |
|---|---|---|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit Cloud creds | LiveKit Cloud → Settings |
| `ANTHROPIC_API_KEY` | **Claude key — preferred LLM** | console.anthropic.com |
| `ANTHROPIC_MODEL` | Claude model (default `claude-haiku-4-5`) | — |
| `GROQ_API_KEY` / `OPENAI_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` | LLM fallback (used only if no Anthropic key) | console.groq.com |
| `DEEPGRAM_API_KEY` | STT + TTS | console.deepgram.com |
| `DEEPGRAM_STT_MODEL` / `DEEPGRAM_TTS_VOICE` | defaults `nova-2` / `aura-asteria-en` | — |
| `DATABASE_URL` | Async Postgres DSN (`postgresql+asyncpg://…`) | matches the `docker run` above |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Twilio creds | console.twilio.com |
| `TWILIO_PHONE_NUMBER` / `TWILIO_HUMAN_AGENT_NUMBER` | dial-from / dial-to (E.164) | Twilio / your phone |
| `LIVEKIT_SIP_TRUNK_ID` | outbound SIP trunk (enables the real audio bridge) | `scripts/setup_sip_trunk.py` |
| `TWILIO_SIP_TERMINATION_URI` / `TWILIO_SIP_USERNAME` / `TWILIO_SIP_PASSWORD` | Twilio Elastic SIP Trunk | Twilio → Elastic SIP Trunking |
| `PUBLIC_BASE_URL` | public URL for Twilio webhooks (ngrok in dev) | ngrok |
| `APP_ENV` / `PORT` / `LOG_LEVEL` | app config | — |

---

## 🧪 Tests & CI

```bash
PYTHONPATH=backend python backend/tests/test_unit.py     # pure functions — no services needed (runs in CI)
PYTHONPATH=backend python backend/tests/test_api.py      # needs the backend on :8000
PYTHONPATH=backend python backend/tests/test_booking.py  # needs PostgreSQL (book/reschedule/cancel/lookup)
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the frontend type-check and the
backend unit tests on every push.

---

## 📁 Project layout

```
voice-agent/
├── backend/
│   ├── agent.py          LiveKit agent worker — VoiceAgent + @function_tool methods
│   ├── main.py           FastAPI — token, webhook, appointments, calls, Twilio TwiML
│   ├── config.py         Pydantic settings — all env vars
│   ├── database/         async SQLAlchemy models + session
│   ├── tools/            appointment tools (check/book/reschedule/cancel/lookup)
│   ├── services/         monitoring (data channel) + transfer (SIP / Twilio)
│   ├── scripts/          setup_sip_trunk.py, reset_room.py
│   └── tests/            test_unit · test_api · test_booking
├── frontend/
│   ├── app/page.tsx      caller call UI
│   ├── app/monitor/      watcher dashboard (transcript, state, latency, take-over)
│   ├── app/calls/        call history + summaries
│   ├── app/bookings/     all appointments
│   ├── app/components/   shared UI (NavBar)
│   └── lib/livekit.ts    token + data-channel helpers
├── docs/                 architecture.md · demo-walkthrough.md
├── .github/workflows/    ci.yml
├── docker-compose.yml · .env.example · CLAUDE.md
```

---

## 🧰 Tech stack

**Real-time:** LiveKit Cloud (WebRTC rooms, Agents 1.6, SIP) · **LLM:** Anthropic Claude Haiku 4.5
(Groq/OpenAI fallback) · **Speech:** Deepgram Nova-2 STT + Aura TTS · **Turn-taking:** Silero VAD +
ONNX turn detector · **Telephony:** Twilio (Elastic SIP Trunk + REST) · **Backend:** Python,
FastAPI, LiveKit Agents SDK, SQLAlchemy (async) · **DB:** PostgreSQL · **Frontend:** Next.js 14,
React, Tailwind, `@livekit/components-react`.
