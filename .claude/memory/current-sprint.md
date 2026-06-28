# Current Sprint — Voice Agent

Last updated: 2026-06-27

## Goal
Conversational voice agent (Agent A) on LiveKit: appointment booking, live
monitoring dashboard, watcher takeover, Twilio warm transfer, post-call summary.

## Status by phase

### Phase 1 — Backend foundation ✅ COMPLETE
- Project structure, CLAUDE.md, README, .env.example, docs/architecture.md
- LiveKit agent (`backend/agent.py`) — Deepgram STT/TTS, Groq LLM, Silero VAD
- Conversation loop working end to end
- Booking tool calls with PostgreSQL storage (`backend/tools/appointment.py`)
- FastAPI endpoints (`backend/main.py`): /api/token, /api/webhook,
  /api/appointments, /api/calls, /health, Twilio TwiML webhooks

### Phase 2 — Frontend + real-time ✅ COMPLETE
- Next.js app (`frontend/`), LiveKit React SDK
- Caller UI (`app/page.tsx`): join room, live transcript, agent-state badge
- Monitoring dashboard (`app/monitor/page.tsx`): transcript, state, intent,
  call status, collected-data panel, post-call summary
- Shared helper `lib/livekit.ts`
- Verified: `tsc --noEmit` clean, both routes serve HTTP 200

### Phase 3 — Takeover + transfer + polish 🟡 IN PROGRESS
- ✅ Watcher takeover (TAKEOVER_REQUEST / TAKEOVER_END over data channel)
- ✅ Post-call summary (LLM, on shutdown callback)
- ✅ Twilio warm transfer: PSTN dial + summary + accept/decline (DTMF)
- 🟡 Real audio bridge via LiveKit SIP (`dial_human_into_room`) — added; needs
  LIVEKIT_SIP_TRUNK_ID configured + live verification
- ✅ README with setup instructions
- ✅ docker-compose.yml

## Key runtime facts (hard-won — see CLAUDE.md for detail)
- livekit-agents **1.6.4** (not 0.x) — API differs; migration table in CLAUDE.md
- Explicit dispatch: worker `agent_name="voice-agent"` + token `RoomConfiguration`.
  Mismatch = caller stuck on "connecting".
- LLM: Groq **llama-3.1-8b-instant** (70b hit 12k TPM 429s under load)
- Deepgram key has **aura-1 only** — use `aura-asteria-en` (aura-2 → 403)
- Twilio `PUBLIC_BASE_URL` must be a live ngrok URL; agent reads it at startup
  (restart agent after changing). Trial accounts play a mandatory message.
