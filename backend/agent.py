"""
Voice Agent (Agent A) — LiveKit Agents worker process.

Run with:
    python agent.py start

One worker process handles multiple concurrent rooms. Each room gets its own
VoiceAgent instance. The pipeline is: VAD → STT → LLM (with tools) → TTS.
Monitoring events are pushed to the room data channel in parallel.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Annotated

from dotenv import load_dotenv

load_dotenv()

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
)
from livekit.agents import metrics as lk_metrics
from livekit.agents.llm import function_tool
from livekit.plugins import anthropic, deepgram, openai as lk_openai, silero
from livekit.plugins.turn_detector.english import EnglishModel

from sqlalchemy import select

from config import settings
from database.models import CallSession
from database.session import AsyncSessionLocal, create_tables
from services.monitoring import (
    MONITOR_TOPIC,
    publish_agent_state,
    publish_booking_update,
    publish_call_status,
    publish_event,
    publish_transcript,
)
from services.transfer import HUMAN_AGENT_IDENTITY, initiate_transfer
from tools.appointment import (
    book_appointment_impl,
    cancel_appointment_impl,
    check_availability_impl,
    lookup_appointment_impl,
    reschedule_appointment_impl,
)

logging.basicConfig(level=getattr(logging, settings.log_level))
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Alex, a warm, upbeat, and genuinely helpful voice receptionist.

Your job is to help callers book, look up, reschedule, and cancel appointments,
and to connect them to a human agent when they need extra help.

## If the caller asks what you can do
Tell them warmly that you can book a new appointment, look up an existing one,
reschedule it, or cancel it — and connect them to a human if they'd like.

## What you collect for bookings
1. Full name
2. Reason for visit (keep it brief — e.g. "annual check-up", "billing question")
3. Preferred date (ask for a specific date; clarify if ambiguous)
4. Preferred time slot (offer options if requested)
5. Contact phone number

## Booking flow
- Collect all five fields above before calling check_availability.
- After confirming availability, call book_appointment to confirm.
- Use EXACT time-slot labels like "9:00 AM" or "4:00 PM" when calling
  check_availability and book_appointment — never vague terms like "morning"
  or "afternoon".
- Read the confirmation number back to the caller slowly and clearly.

## Reading numbers aloud
- ALWAYS read phone numbers and confirmation numbers ONE CHARACTER AT A TIME,
  e.g. phone "six, three, seven, four, …" and confirmation "A-P-T dash A-C-3-9".
- Never say a phone or confirmation number as a single large number.

## Managing existing appointments
- If the caller asks about existing appointments, ask for their phone number
  and call lookup_appointment, then read back their upcoming appointments.
- If the caller wants to reschedule, ask for their confirmation number, then
  their new date and time preference, and call reschedule_appointment.
- If the caller wants to cancel, ask for their confirmation number and confirm
  with them before calling cancel_appointment.

## When to transfer
Only transfer when the caller clearly wants a human — do NOT transfer just
because a word like "billing" appears. Transfer when the caller:
- Explicitly asks for "a person", "human", "agent", or "representative"
- Wants to dispute a charge / refund, or raises a complaint they want escalated
- Is upset, rude, or repeatedly frustrated
Do NOT transfer when "billing", "payment", or "refund" is simply given as the
reason for a routine appointment — that is a normal booking; just continue.

## Edge cases — handle gracefully
- Past date requested: say "I'm sorry, I can only book future appointments,"
  then ask for a day that's coming up.
- Weekend requested: say "We're only available Monday to Friday," then offer
  the nearest weekday.
- Requested slot unavailable: apologize briefly and offer the next available
  slot from check_availability's alternatives.
- Caller gives only partial info: thank them, then ask for the next single
  missing item — never list everything still needed at once.
- Caller is rude or frustrated: stay calm and kind, don't argue, apologize for
  the trouble, and offer to connect them to a human agent.

## Conversation style & personality
- You are Alex: warm, upbeat, and reassuring — a voice interface, not a chat bot.
- Use natural, friendly acknowledgements: "Perfect!", "Got it!", "Great choice!".
- Ask for only ONE piece of information at a time; confirm each answer before moving on.
- If the caller offers several details at once, accept them and confirm them back.
- If the caller is unsure of a date, offer "Would tomorrow work?" as a fallback.
- Keep responses under 2 sentences unless reading back a confirmation.
- When transferring, be apologetic and reassuring: "I'm sorry you've had trouble —
  let me connect you to a human who can help. Please stay on the line."

## Always follow the tool result — never fabricate
- If check_availability returns available=false, tell the caller that slot is
  taken and offer the alternatives it lists. Do NOT proceed to book it.
- If book_appointment returns success=false, tell the caller it did not go
  through and why (e.g. the slot was just taken), then offer another slot.
- ONLY say "booked / confirmed" and read back a confirmation number when
  book_appointment returns success=true — and use the EXACT confirmation_number
  from that result. Never reuse or invent a confirmation number.

## Constraints
- Today's date is {today}. A date AFTER today is in the future and is valid;
  only refuse dates strictly before today. Reason about this carefully.
- Only offer slots Monday–Friday, 9 AM–5 PM (no weekends).
- Never write function calls as text — call the tools properly.
"""


# ── Agent class ───────────────────────────────────────────────────────────────

class VoiceAgent(Agent):
    """
    Appointment booking voice agent.

    Holds all per-call state: paused flag (for takeover), collected booking
    data (echoed to monitoring dashboard), and the room reference for publishing
    data channel events.
    """

    def __init__(self) -> None:
        # @function_tool-decorated methods below are auto-discovered by
        # Agent.__init__ via find_function_tools(self) — no explicit tools= needed.
        super().__init__(
            instructions=SYSTEM_PROMPT.format(
                today=datetime.now().strftime("%A, %B %d %Y")
            ),
        )
        self._paused: bool = False
        self._room: rtc.Room | None = None
        self._collected: dict[str, str] = {}
        self._human_joined_at: float | None = None

    # ── Tools (methods so they can publish to the dashboard + track state) ──────

    @function_tool
    async def check_availability(
        self,
        date: Annotated[str, "Appointment date in YYYY-MM-DD format"],
        time_slot: Annotated[str, "Requested time slot, e.g. '10:00 AM' or '2:30 PM'"],
    ) -> str:
        """Check whether a specific date and time slot is available for booking."""
        result = await check_availability_impl(date, time_slot)
        # Surface the slot the caller is interested in to the dashboard.
        if self._room:
            await publish_booking_update(self._room, "date", date)
            await publish_booking_update(self._room, "time_slot", time_slot)
        self._collected["date"] = date
        self._collected["time_slot"] = time_slot
        return json.dumps(result)

    @function_tool
    async def book_appointment(
        self,
        name: Annotated[str, "Caller's full name"],
        reason: Annotated[str, "Brief reason for the appointment"],
        date: Annotated[str, "Confirmed appointment date in YYYY-MM-DD format"],
        time_slot: Annotated[str, "Confirmed time slot, e.g. '10:00 AM'"],
        phone: Annotated[str, "Caller's contact phone number"],
    ) -> str:
        """Book a confirmed appointment slot after availability has been verified."""
        result = await book_appointment_impl(
            name, reason, date, time_slot, phone,
            room_name=self._room.name if self._room else None,
        )
        # Track collected booking data and echo each field to the dashboard.
        fields = {
            "name": name,
            "reason": reason,
            "date": date,
            "time_slot": time_slot,
            "phone": phone,
        }
        self._collected.update(fields)
        if self._room:
            for field, value in fields.items():
                await publish_booking_update(self._room, field, value)
        return json.dumps(result)

    @function_tool
    async def request_human_transfer(
        self,
        reason: Annotated[str, "Why the caller needs a human agent (billing/complaint/preference)"],
        caller_name: Annotated[str, "Caller's name if already collected, otherwise 'the caller'"],
    ) -> str:
        """Initiate a warm transfer to a human agent via Twilio."""
        self._collected["transfer_reason"] = reason
        if caller_name and caller_name != "the caller":
            self._collected["name"] = caller_name
        # Fire the Twilio dial directly when the tool is invoked — deterministic.
        # Do NOT depend on the agent's spoken wording to trigger the call. Run it
        # as a background task so dialing happens while the agent speaks its reply.
        asyncio.ensure_future(self._handle_transfer_tool_result(reason, caller_name))
        return json.dumps({
            "status": "transfer_initiated",
            "reason": reason,
            "caller_name": caller_name,
            "message": (
                "I'm sorry you've had trouble — let me connect you to a human agent "
                "who can help. Please stay on the line, this will just take a moment."
            ),
        })

    @function_tool
    async def lookup_appointment(
        self,
        phone: Annotated[str, "Caller's contact phone number to look up appointments for"],
    ) -> str:
        """Look up a caller's upcoming appointments by their phone number."""
        appointments = await lookup_appointment_impl(phone)
        return json.dumps({"appointments": appointments, "count": len(appointments)})

    @function_tool
    async def reschedule_appointment(
        self,
        confirmation_number: Annotated[str, "Existing appointment confirmation number, e.g. 'APT-AC39CA4E'"],
        new_date: Annotated[str, "New appointment date in YYYY-MM-DD format"],
        new_time: Annotated[str, "New time slot, e.g. '10:00 AM'"],
    ) -> str:
        """Reschedule an existing appointment to a new date and time."""
        result = await reschedule_appointment_impl(confirmation_number, new_date, new_time)
        return json.dumps(result)

    @function_tool
    async def cancel_appointment(
        self,
        confirmation_number: Annotated[str, "Existing appointment confirmation number, e.g. 'APT-AC39CA4E'"],
    ) -> str:
        """Cancel an existing appointment by its confirmation number."""
        result = await cancel_appointment_impl(confirmation_number)
        return json.dumps(result)

    async def on_enter(self) -> None:
        """Greet the caller and signal connected status to the dashboard."""
        if self._room:
            await publish_call_status(self._room, "connected")
        await self.session.say(
            "Hi there, thanks for calling! I'm Alex, your scheduling assistant. "
            "I can book a new appointment for you, or look up, reschedule, or cancel "
            "an existing one. What can I do for you today?"
        )

    async def on_user_turn_completed(
        self,
        turn_ctx: "agents.llm.ChatContext",
        new_message: "agents.llm.ChatMessage",
    ) -> None:
        """Suppress the agent's reply while paused (watcher takeover or human handoff).

        Raising StopResponse stops the LLM from generating a turn, so the agent
        stays silent while a human (watcher or SIP agent) handles the caller.
        Without this the agent would keep replying over the human.
        """
        if self._paused:
            raise agents.StopResponse()

    def set_room(self, room: rtc.Room) -> None:
        """Store the room reference so monitoring events can be published."""
        self._room = room

    async def handle_takeover_request(self) -> None:
        """Pause the agent so the watcher can speak directly to the caller."""
        self._paused = True
        # The session raises if the call already ended (no activity context).
        try:
            self.session.interrupt()
        except RuntimeError:
            logger.info("Takeover requested but session is no longer active — ignoring")
            return
        if self._room:
            await publish_event(self._room, "call_status", {"status": "takeover_active"})
        logger.info("Agent paused — watcher takeover active")

    async def handle_takeover_end(self) -> None:
        """Resume normal operation after the watcher hands back control."""
        self._paused = False
        if self._room:
            await publish_event(self._room, "call_status", {"status": "connected"})
        # The session raises if the call already ended (no activity context).
        try:
            await self.session.say("I'm back. How can I continue helping you?")
        except RuntimeError:
            logger.info("Takeover end requested but session is no longer active — ignoring")
            return
        logger.info("Agent resumed — watcher handed back")

    async def _handle_transfer_tool_result(self, reason: str, caller_name: str) -> None:
        """Fire-and-forget: dial the human agent after the LLM transfer tool runs.

        Publishes 'initiated' immediately; 'accepted' is published later by
        handle_human_joined() when the human actually joins the room (SIP path).
        """
        if not self._room:
            return
        await publish_event(self._room, "transfer_status", {"status": "initiated", "reason": reason})
        result = await initiate_transfer(
            room_name=self._room.name,
            caller_name=caller_name,
            transfer_reason=reason,
        )
        # Both SIP and Twilio paths return {"status": "initiated"} on success.
        if result.get("status") != "initiated":
            await publish_event(self._room, "transfer_status", {"status": "error", **result})

    async def handle_human_joined(self) -> None:
        """A human agent joined the room over SIP — pause the AI and hand over.

        From here the human and the caller talk directly in the same LiveKit
        room; the AI stays silent (see on_user_turn_completed + _paused).
        """
        self._paused = True
        self._human_joined_at = time.monotonic()
        try:
            self.session.interrupt()
        except RuntimeError:
            pass
        if self._room:
            await publish_event(self._room, "transfer_status", {"status": "accepted"})
            await publish_event(self._room, "call_status", {"status": "transferring"})
        logger.info("Human agent joined room — AI paused, caller handed over")

    async def handle_human_left(self) -> None:
        """The human SIP leg left — resume the AI for the caller.

        If the leg lasted only a moment it never really connected (e.g. the SIP
        call failed at Twilio — auth/no-answer), so we apologise and resume
        rather than saying "thanks for holding" as if a real handoff happened.
        """
        self._paused = False
        joined_at, self._human_joined_at = self._human_joined_at, None
        failed = joined_at is not None and (time.monotonic() - joined_at) < 5.0

        if self._room:
            status = "error" if failed else "ended"
            await publish_event(self._room, "transfer_status", {"status": status})
            await publish_event(self._room, "call_status", {"status": "connected"})
        message = (
            "I'm sorry, I couldn't reach a human agent right now. "
            "Is there anything else I can help you with?"
            if failed
            else "Thanks for holding. How else can I help you?"
        )
        try:
            await self.session.say(message)
        except RuntimeError:
            pass
        logger.info(
            "Human SIP leg left (%s) — AI resumed",
            "transfer failed" if failed else "handoff ended",
        )


# ── Session wiring ─────────────────────────────────────────────────────────────

async def _wire_session_events(
    session: AgentSession,
    agent: VoiceAgent,
    room: rtc.Room,
) -> None:
    """
    Attach callbacks to AgentSession events for live monitoring.

    All callbacks are sync (the SDK fires them synchronously); we schedule
    async publishing with asyncio.ensure_future so we never block the event loop.
    """

    @session.on("conversation_item_added")
    def on_conversation_item(event: agents.ConversationItemAddedEvent) -> None:
        """Forward finalised transcript turns (user + agent) to the dashboard."""
        item = event.item
        # Only ChatMessage items carry transcript text (skip AgentHandoff etc.)
        if getattr(item, "type", None) != "message":
            return
        text = item.text_content or ""
        if not text:
            return

        if item.role == "user":
            if agent._paused:
                return
            asyncio.ensure_future(publish_transcript(room, "user", text))
            # Detect intent from keywords for dashboard display
            text_lower = text.lower()
            if any(w in text_lower for w in ["book", "appointment", "schedule", "visit"]):
                asyncio.ensure_future(
                    publish_event(room, "intent", {"intent": "booking"})
                )
            elif any(
                w in text_lower
                for w in ["bill", "complaint", "human", "person", "agent"]
            ):
                asyncio.ensure_future(
                    publish_event(room, "intent", {"intent": "transfer"})
                )
        else:  # assistant / agent turn
            asyncio.ensure_future(publish_transcript(room, "agent", text))
            # NOTE: the Twilio transfer is fired directly inside the
            # request_human_transfer tool (deterministic). We no longer trigger
            # it by string-matching the agent's spoken words, which was brittle —
            # the LLM phrasing the reply differently would silently skip the call.

    @session.on("agent_state_changed")
    def on_state_changed(event: agents.AgentStateChangedEvent) -> None:
        """Publish agent state (listening/thinking/speaking) to dashboard."""
        asyncio.ensure_future(
            publish_agent_state(room, event.new_state)
        )

    @session.on("metrics_collected")
    def on_metrics(event: MetricsCollectedEvent) -> None:
        """Publish per-turn pipeline latency (LLM TTFT, TTS TTFB, STT/EOU delay).

        The SDK emits one metric object per stage per turn; we forward each as a
        partial 'turn_metrics' event and let the dashboard merge them.
        """
        m = event.metrics
        data: dict[str, float] = {}
        if isinstance(m, lk_metrics.LLMMetrics):
            if m.ttft and m.ttft > 0:
                data["llm_ttft_ms"] = round(m.ttft * 1000, 1)
        elif isinstance(m, lk_metrics.TTSMetrics):
            if m.ttfb and m.ttfb > 0:
                data["tts_ttfb_ms"] = round(m.ttfb * 1000, 1)
        elif isinstance(m, lk_metrics.EOUMetrics):
            if m.transcription_delay:
                data["stt_ms"] = round(m.transcription_delay * 1000, 1)
            if m.end_of_utterance_delay:
                data["eou_ms"] = round(m.end_of_utterance_delay * 1000, 1)
        if data:
            asyncio.ensure_future(publish_event(room, "turn_metrics", data))


async def _wire_data_channel(agent: VoiceAgent, room: rtc.Room) -> None:
    """
    Listen for watcher control messages on the room data channel, and for the
    human agent joining/leaving the room over SIP (the warm-transfer bridge).

    TAKEOVER_REQUEST → pause agent          (watcher)
    TAKEOVER_END     → resume agent          (watcher)
    human-agent joins → pause agent, hand over (SIP warm transfer)
    human-agent leaves → resume agent
    """
    @room.on("data_received")
    def on_data(data_packet: rtc.DataPacket) -> None:
        try:
            message = json.loads(data_packet.data.decode("utf-8"))
            msg_type = message.get("type")
            if msg_type == "TAKEOVER_REQUEST":
                asyncio.ensure_future(agent.handle_takeover_request())
            elif msg_type == "TAKEOVER_END":
                asyncio.ensure_future(agent.handle_takeover_end())
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Ignore malformed messages

    def _is_human_agent(participant: rtc.RemoteParticipant) -> bool:
        """True when the participant is the warm-transferred human (SIP leg)."""
        return (
            participant.identity == HUMAN_AGENT_IDENTITY
            or participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        )

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        if _is_human_agent(participant):
            asyncio.ensure_future(agent.handle_human_joined())

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        if _is_human_agent(participant):
            asyncio.ensure_future(agent.handle_human_left())


# ── Entrypoint ────────────────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext) -> None:
    """
    Called by the LiveKit worker for each new room job.

    Connects to the room, builds the STT→LLM→TTS pipeline, and starts the
    VoiceAgent. Blocks until the room disconnects.
    """
    logger.info("Agent job started for room: %s", ctx.room.name)

    await ctx.connect(auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)

    agent = VoiceAgent()
    agent.set_room(ctx.room)

    # LLM: prefer Claude (Anthropic) when configured — reliable tool-calling and
    # low latency on Haiku, which the booking/reschedule/cancel/lookup tools need.
    # Falls back to the Groq/OpenAI-compatible path when no Anthropic key is set.
    if settings.use_anthropic:
        llm = anthropic.LLM(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
        )
    else:
        llm = lk_openai.LLM(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )

    # Build the speech pipeline
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model=settings.deepgram_stt_model),
        llm=llm,
        tts=deepgram.TTS(model=settings.deepgram_tts_voice),
        # Semantic end-of-turn detection (ONNX transformer) on top of Silero VAD:
        # VAD detects silence; this model reads the transcript to decide if the
        # caller actually finished their thought — stops the agent cutting in on
        # mid-sentence pauses. Needs `python agent.py download-files` once.
        turn_detection=EnglishModel(),
    )

    # Wire monitoring and takeover callbacks before starting
    await _wire_session_events(session, agent, ctx.room)
    await _wire_data_channel(agent, ctx.room)

    # Generate a post-call summary when the job shuts down (room disconnects).
    # v1.x keeps the job alive after start() returns; shutdown callbacks fire
    # when the room closes — there is no session.wait_for_disconnect().
    async def _on_shutdown() -> None:
        await _generate_summary(session, ctx.room)

    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(
        agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(),
    )
    # Note: the agent greets the caller via VoiceAgent.on_enter() — do not add
    # another greeting here or the caller hears two hellos.


async def _generate_summary(session: AgentSession, room: rtc.Room) -> None:
    """Generate a post-call LLM summary and publish it to the data channel."""
    try:
        history = session.history
        messages = history.messages() if history else []
        if not messages:
            return

        # Build a condensed transcript for the summary prompt
        lines = []
        for msg in messages:
            role = "Caller" if msg.role == "user" else "Agent"
            content = msg.text_content or ""
            if content:
                lines.append(f"{role}: {content}")

        transcript_text = "\n".join(lines[-40:])  # last 40 turns

        summary_llm = lk_openai.LLM(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )

        # Use the LLM to generate a summary
        summary_ctx = agents.llm.ChatContext.empty()
        summary_ctx.add_message(
            role="user",
            content=(
                "You are a call centre supervisor. Summarise this customer service call "
                "in 3–5 bullet points. Include: purpose of call, outcome, any appointments "
                "booked (with confirmation numbers), and any unresolved issues.\n\n"
                f"TRANSCRIPT:\n{transcript_text}"
            ),
        )

        summary_text = ""
        async with summary_llm.chat(chat_ctx=summary_ctx) as response:
            async for chunk in response:
                if chunk.delta and chunk.delta.content:
                    summary_text += chunk.delta.content

        # Persist to the CallSession row so the Call History page can show it.
        await _persist_call_summary(room.name, summary_text)

        await publish_event(room, "call_status", {
            "status": "ended",
            "summary": summary_text,
        })
        logger.info("Post-call summary generated for room %s", room.name)

    except Exception as exc:
        logger.error("Failed to generate post-call summary: %s", exc)
        await publish_call_status(room, "ended")


async def _persist_call_summary(room_name: str, summary: str) -> None:
    """Write the post-call summary (and ended state) to the CallSession row.

    Upserts by room_name: the row is normally created by the LiveKit
    room_started webhook, but we create it here too so the summary is never lost
    if the webhook didn't fire. Failures are logged, never raised — persistence
    must not break the call teardown.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CallSession).where(CallSession.room_name == room_name)
            )
            call = result.scalars().first()
            if call is None:
                call = CallSession(room_name=room_name, status="ended")
                db.add(call)
            call.summary = summary
            call.status = "ended"
            call.ended_at = datetime.now(timezone.utc)
            await db.commit()
        logger.info("Call summary persisted for room %s", room_name)
    except Exception as exc:
        logger.error("Failed to persist call summary for %s: %s", room_name, exc)


# ── Worker entrypoint ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # agent_name enables EXPLICIT dispatch: the agent only joins rooms it is
    # explicitly dispatched to. The backend bakes a RoomAgentDispatch for this
    # name into each caller's token (see main.py /api/token), so the agent is
    # guaranteed to join the exact room the caller creates — deterministic,
    # unlike automatic dispatch which relies on flaky cloud worker selection.
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="voice-agent",
        )
    )
