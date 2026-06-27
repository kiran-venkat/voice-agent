# Voice Agent — Project Index

## Purpose

Conversational voice agent for appointment booking with live monitoring and warm
transfer. Caller talks to Agent A (LiveKit + Deepgram + Groq), a watcher monitors
in real time via a Next.js dashboard, and billing/complaint escalations are warm-
transferred to a human agent via Twilio.

## Stack

| Layer      | Technology                                              |
|------------|---------------------------------------------------------|
| Agent      | Python 3.13 · **livekit-agents 1.6.4** · Deepgram STT/TTS |
| LLM        | Groq **llama-3.1-8b-instant** (OpenAI-compatible API)   |
| VAD        | Silero VAD (bundled — explicit `silero` plugin deprecated) |
| API        | FastAPI + uvicorn                                       |
| Database   | PostgreSQL (asyncpg) + SQLAlchemy async                 |
| Cache      | —                                                       |
| Transfer   | Twilio (PSTN) + LiveKit SIP (audio bridge into room)    |
| Frontend   | Next.js · @livekit/components-react · Tailwind CSS      |

> ⚠️ The codebase originally targeted livekit-agents 0.x; it now runs **1.6.4**,
> which changed many APIs. See "livekit-agents 1.6.4 API patterns" below before
> editing `agent.py`.

## Architecture (one line per layer)

Caller → LiveKit Room → VoiceAgent (STT→LLM→TTS) → Tool calls → PostgreSQL
                     ↓ data channel events
Monitor Browser → Next.js Dashboard → takeover/resume signals → Agent pauses
Agent → Twilio REST → Human Agent Phone → TwiML conference bridge

## Directory Map

```
voice-agent/
├── backend/
│   ├── agent.py          ← LiveKit agent entrypoint (VoiceAgent class + tools)
│   ├── main.py           ← FastAPI app (/token /webhook /appointments /calls)
│   ├── config.py         ← Pydantic settings (all env vars)
│   ├── database/
│   │   ├── models.py     ← SQLAlchemy models: Appointment, CallSession
│   │   └── session.py    ← Async engine + session factory
│   ├── tools/
│   │   └── appointment.py ← check_availability, book, reschedule, cancel, lookup impls
│   └── services/
│       ├── monitoring.py ← publish_event() — room data channel helper
│       └── transfer.py   ← Twilio outbound call + TwiML warm transfer
├── frontend/             ← Next.js: app/page.tsx (caller), app/monitor (watcher), lib/livekit.ts
├── docs/
│   └── architecture.md
├── .env.example
├── docker-compose.yml
└── CLAUDE.md
```

## Key Decisions

### Agent publishes monitoring events via LiveKit data channel
`services/monitoring.py:publish_event()` serialises JSON and calls
`room.local_participant.publish_data()`. The frontend subscribes with
`useDataChannel`. No separate WebSocket needed — LiveKit is the transport.

### Groq via OpenAI-compatible base URL
`livekit-plugins-openai` supports any OpenAI-compatible endpoint. Groq's
`https://api.groq.com/openai/v1` is used with `GROQ_API_KEY`. Falls back to
`OPENAI_API_KEY` if Groq is not configured.

### Takeover via data channel signals
Watcher sends `{"type":"TAKEOVER_REQUEST"}` to room. Agent receives it via
`@room.on("data_received")`, sets `_paused = True`, and stops generating replies.
Resume sends `{"type":"TAKEOVER_END"}`. No separate signalling server needed.

### Appointments stored in PostgreSQL, not a scheduler API
`tools/appointment.py` queries the `appointments` table for availability and
inserts confirmed bookings. Cal.com integration is a future upgrade path.

### Agent tools (all `@function_tool` methods on `VoiceAgent`, auto-discovered)
- `check_availability(date, time_slot)` — publishes `date`/`time_slot` booking_update.
- `book_appointment(name, reason, date, time_slot, phone)` — publishes all 5 fields.
- `reschedule_appointment(confirmation_number, new_date, new_time)` — re-checks slot.
- `cancel_appointment(confirmation_number)` — sets `status='cancelled'` (idempotent).
- `lookup_appointment(phone)` — upcoming (today+), non-cancelled appts by phone.
- `request_human_transfer(reason, caller_name)` — fires the SIP/Twilio dial.

Confirmation numbers (`APT-<8 hex>`) are **derived from the UUID, not stored** —
`_find_by_confirmation()` resolves them via `cast(id, String) ILIKE 'prefix%'`.
The agent collects appointment fields conversationally; only `check_availability`
(date/time) and `book_appointment` (all fields) emit `booking_update` events, so
the dashboard's "Collecting Info" panel shows name/reason/phone as ✅ at booking
time and derives ⏳/⬜ for the rest. No per-field tool by design.

### Warm transfer: tool fires the dial directly; audio bridge needs LiveKit SIP
The `request_human_transfer` tool fires the human dial **directly when the LLM
invokes it** (`asyncio.ensure_future(self._handle_transfer_tool_result(...))`).
Do NOT trigger the transfer by string-matching the agent's spoken words — that
was the original design and it silently failed whenever the LLM phrased its reply
differently (e.g. "transferred you to a live agent" instead of "connecting you").
The Twilio-conference approach only rings the human; bridging the WebRTC caller
to the human requires **LiveKit SIP** (dial the human into the LiveKit room as a
SIP participant). See "Twilio + warm transfer" below.

### Explicit agent dispatch (not automatic) — the agent reliably joins the room
The worker registers with `agent_name="voice-agent"` (explicit dispatch), and the
backend bakes a `RoomAgentDispatch` into each caller's token via `RoomConfiguration`
(see `main.py /api/token`). This guarantees the agent joins the exact room the
caller creates. Automatic dispatch (empty `agent_name`) proved unreliable here —
it depends on LiveKit Cloud's worker selection and would intermittently leave the
caller stuck on "connecting" with no agent. Do not remove the `agent_name` or the
token `RoomConfiguration` — they are a matched pair.

### All DB queries are async (asyncpg)
`database/session.py` uses `create_async_engine`. Every repository function is
`async def`. No sync DB calls anywhere.

## Code Rules (always apply)

- No `print()` — use `logging.getLogger(__name__)`
- Type hints on every function signature
- `async def` for everything that touches I/O
- Every function has a one-line docstring minimum
- Conventional commits: `feat(scope): description`
- Never log raw API keys or phone numbers
- All env vars in `config.py` — never `os.getenv()` directly in business logic

## Data Channel Event Schema

All events published to the LiveKit room data channel follow this shape:

```json
{
  "type": "transcript | agent_state | intent | booking_update | call_status | transfer_status",
  "data": { ... },
  "ts": "2026-06-27T10:00:00Z"
}
```

### Event types

| type             | data fields                                              |
|------------------|----------------------------------------------------------|
| transcript       | role ("user"\|"agent"), text, final (bool)               |
| agent_state      | state ("listening"\|"thinking"\|"speaking")              |
| intent           | intent ("booking"\|"transfer"\|"general"), confidence    |
| booking_update   | field, value (tracks collected data in real time)        |
| call_status      | status ("connected"\|"transferring"\|"ended")            |
| transfer_status  | status ("initiated"\|"accepted"\|"declined")             |

---

## Running the stack (3 processes)

All three must run at once. Start backend + agent **before** opening the browser —
if a caller joins before the agent worker is registered, the room is created
without an agent and the caller is stuck on "connecting".

**Terminal 1 — FastAPI backend (token server + Twilio webhooks), port 8000:**
```bash
cd ~/Documents/Kiran_Personal/voice-agent
PYTHONPATH=backend python backend/main.py
# wait for: "Application startup complete"
```

**Terminal 2 — Agent worker, port 8081:**
```bash
cd ~/Documents/Kiran_Personal/voice-agent
PYTHONPATH=backend python backend/agent.py start
# wait for: registered worker  "agent_name": "voice-agent"
```

**Terminal 3 — Frontend (Next.js), port 3000 or 3001:**
```bash
cd ~/Documents/Kiran_Personal/voice-agent/frontend
npm run dev        # note the port it prints — often 3001 if 3000 is taken
```

Then open the printed URL (`/` = caller, `/monitor` = watcher dashboard), Join,
allow the mic. The agent (Alex) greets first via `VoiceAgent.on_enter()`.

### Kill everything / reset cleanly
```bash
pkill -f "backend/main.py"; pkill -f "agent.py start"; pkill -f "multiprocessing.spawn"
for p in 8000 8081 3001 3000; do lsof -ti :$p 2>/dev/null | xargs kill -9 2>/dev/null; done
```

### Operational gotchas (cost a lot of debugging time — read this)
- **Always Ctrl+C the agent to stop it** — never just close the terminal. A hard
  kill orphans the worker's `multiprocessing.spawn` child processes, which keep a
  LiveKit Cloud registration alive and **steal job assignments** → caller gets no
  agent (`AssignmentTimeoutError` in logs). If dispatch acts flaky, check for
  stray `agent.py`/`multiprocessing.spawn` processes and the run-the-kill block.
- **Never run two agent workers at once** — same cause as above.
- A **hung FastAPI backend** makes `/api/token` time out → browser sits on
  "connecting" before LiveKit is ever reached. Test with
  `curl --max-time 4 localhost:8000/health`. If it hangs, kill + restart it.
- `livekit.api` room/SIP helpers are handy for diagnosis:
  `list_rooms`, `list_participants`, `remove_participant` (clears a ghost
  participant pinning a dead room), `delete_room`.

## livekit-agents 1.6.4 API patterns

The code was written for 0.x. These are the 1.6.4 equivalents (all already applied
in `agent.py` — follow these when editing):

| Concern | 0.x (old, broken) | 1.6.4 (correct) |
|---|---|---|
| Start session | `session.start(room, agent=agent)` | `session.start(agent, room=room, ...)` — agent is positional |
| Agent state event | `agents.AgentState` enum + `state` arg | `@session.on("agent_state_changed")` → `AgentStateChangedEvent`; use `event.new_state` (plain string) |
| Transcript events | `user_speech_committed` / `agent_speech_committed` | single `conversation_item_added` → `ConversationItemAddedEvent`; branch on `event.item.role`, read `event.item.text_content` |
| Wait for end | `session.wait_for_disconnect()` | does not exist — use `ctx.add_shutdown_callback(fn)` (job stays alive after `start()` returns) |
| History | `session.history.messages` (attr) | `session.history.messages()` (method); message text via `msg.text_content` |
| Build ChatContext | `ChatContext()` + `.append(role=, text=)` | `ChatContext.empty()` + `.add_message(role=, content=)` |
| LLM call | `await llm.chat(...)` then iterate | `async with llm.chat(chat_ctx=...) as stream:` (not awaitable) |
| Stream chunk | `chunk.choices[0].delta.content` | `chunk.delta.content` (`ChatChunk`) |
| Tools | standalone `@function_tool` funcs passed to `tools=[...]` | `@function_tool` **methods** on the `Agent` subclass — auto-discovered via `find_function_tools(self)`, and they get `self` (room, collected state) |
| Greeting | — | `VoiceAgent.on_enter()` (lifecycle hook) calls `self.session.say(...)`. Do NOT also call `session.generate_reply()` in the entrypoint or the caller hears **two** greetings |
| Worker dispatch | `WorkerType.ROOM` (automatic) | `WorkerOptions(agent_name="voice-agent")` (explicit) + token `RoomConfiguration` (see above) |

Tool methods that touch the room (e.g. publishing `booking_update`) must guard on
`self._room` and may raise if the session already ended — wrap `self.session.*`
calls in `try/except RuntimeError` (see `handle_takeover_request/_end`).

## Deepgram STT/TTS config

- Set via env: `DEEPGRAM_STT_MODEL` (default `nova-2`), `DEEPGRAM_TTS_VOICE`.
- **This project's Deepgram key only has access to aura-1 voices.** `aura-2-*`
  returns `403 "Project does not have access to the requested model"` and crashes
  the TTS task. Use `aura-asteria-en` (or another aura-1 voice). Verify a voice
  with: `curl -X POST "https://api.deepgram.com/v1/speak?model=<voice>" -H "Authorization: Token $KEY" -d '{"text":"hi"}'` → expect HTTP 200.

## Twilio + warm transfer

### Current behaviour (PSTN dial + accept/decline)
1. LLM calls `request_human_transfer` → `initiate_transfer()` (runs in the **agent**
   process) creates a Twilio call to `TWILIO_HUMAN_AGENT_NUMBER`.
2. Human answers → Twilio fetches TwiML from `{PUBLIC_BASE_URL}/api/twilio/transfer-answer`
   → plays summary + `Gather` (press 1 accept / 2 decline).
3. Press 1 → `/api/twilio/transfer-keypress` → `<Dial><Conference>` TwiML.

### Twilio gotchas (each cost real debugging time)
- **`PUBLIC_BASE_URL` must be a public URL reachable by Twilio** (use `ngrok http 8000`).
  The placeholder `https://your-ngrok-url.ngrok-free.app` → Twilio can't fetch TwiML
  → dead air after the trial message. The URL is read from settings by the **agent
  process at startup**, so after changing it you must **restart the agent**.
  Free ngrok URLs change on every ngrok restart — keep `.env` in sync + restart.
- **Trial accounts** play a "you have a trial account" message and require the callee
  to **press a key** before the app's TwiML runs. A ~13s call that's just the trial
  message = no key pressed (or unreachable TwiML). Upgrade the account to remove it.
- **URL-encode TwiML query params.** A `caller` name with a space produced Twilio
  error **11100 Invalid URL** ("application error has occurred"). `build_transfer_answer_twiml`
  now uses `urllib.parse.urlencode` + `xml.sax.saxutils.escape`. Never f-string raw
  values into a TwiML URL or spoken text.
- Diagnose Twilio failures via the API: `client.calls.list()` (status/duration —
  ~13s completed = trial message only) and `client.monitor.v1.alerts.list()`
  (exact `error_code`, e.g. 11100 invalid URL, 11200 unreachable, 12100 bad XML).

### Why a Twilio conference does NOT bridge the caller
The caller is on **WebRTC (LiveKit)**; the human is on **PSTN (Twilio)**. Putting
the human in a Twilio `<Conference>` leaves them alone (hold music) because the
WebRTC caller is never in that conference.

### Real warm-transfer bridge via LiveKit SIP (implemented)
`dial_human_into_room()` in `services/transfer.py` uses `api.CreateSIPParticipant`
to dial the human's phone into the **same LiveKit room** as the caller over an
outbound SIP trunk. When the human answers, `agent.py` detects them joining
(`participant_connected`, identity `human-agent` / kind SIP) and pauses the AI
(`handle_human_joined` → `_paused=True`; `on_user_turn_completed` raises
`StopResponse` so the AI stays silent). The human and caller then talk directly.
`initiate_transfer()` uses this path when `LIVEKIT_SIP_TRUNK_ID` is set, else it
falls back to the Twilio REST accept/decline flow (no real bridge).

#### One-time setup
1. **Twilio Console → Elastic SIP Trunking → Trunks → Create:**
   - **Termination**: set a Termination SIP URI, e.g. `my-trunk.pstn.twilio.com`.
   - **Termination → Authentication**: add a **Credential List** (username +
     password). Credential auth is simpler than IP ACL (LiveKit Cloud IPs vary).
   - Ensure the account can place outbound calls; on a **trial account the
     destination (human) number must be verified**.
2. **Set in `.env`:**
   ```
   TWILIO_SIP_TERMINATION_URI=my-trunk.pstn.twilio.com
   TWILIO_SIP_USERNAME=<credential-list username>
   TWILIO_SIP_PASSWORD=<credential-list password>
   TWILIO_PHONE_NUMBER=+1...        # outbound caller ID
   TWILIO_HUMAN_AGENT_NUMBER=+91...  # who gets dialed (verified on trial)
   ```
3. **Register the LiveKit outbound trunk (one time):**
   ```bash
   PYTHONPATH=backend python backend/scripts/setup_sip_trunk.py
   ```
   It prints `LIVEKIT_SIP_TRUNK_ID=ST_...`. Add that to `.env`.
4. **Restart the agent** (it reads `LIVEKIT_SIP_TRUNK_ID` at startup).

#### Expected behaviour after setup
Caller asks for a human → AI says "connecting you" → the human's phone rings →
human answers → human + caller are talking live in the room, AI is silent. If the
human hangs up, the AI resumes (`handle_human_left`).

#### Notes / limitations
- This is a "blind" warm transfer — the human is dropped straight into the call.
  A pre-connect summary IVR to the human would need a SIP dispatch rule / extra
  TwiML and is not implemented.
- SIP outbound incurs Twilio PSTN charges.
- The legacy Twilio-conference TwiML (`build_conference_twiml`) and
  `add_caller_to_conference` stub are dead once SIP is configured.

## Known issues & next steps

- **Groq free tier = 12,000 TPM** on `llama-3.3-70b-versatile` → `APIConnectionError`
  / 429 under load. Mitigated by using `llama-3.1-8b-instant` (lighter, faster, good
  for voice latency). For heavier reasoning, trim the system prompt + truncate
  history sent per turn, or upgrade the Groq tier.
- **`top`-of-call latency:** logs show occasional `inference is slower than realtime`
  and `eou detection ran after ... flushed` — acceptable on free tiers; raise
  `min_delay` endpointing if STT finals lag.
- **Deprecation warnings** (`silero` VAD `vad=` arg, `RoomInputOptions`) are
  harmless; migrate to bundled VAD / `RoomOptions` when convenient.
- **Warm-transfer audio bridge** via LiveKit SIP — in progress (replaces the
  Twilio-conference dead-end).
