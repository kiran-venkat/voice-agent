"""
FastAPI application — token generation, webhooks, and REST API.

Endpoints:
  POST /api/token                      — LiveKit access token
  POST /api/webhook                    — LiveKit webhook receiver
  GET  /api/appointments               — list all appointments
  GET  /api/appointments/{id}          — single appointment
  GET  /health                         — liveness probe
  POST /api/twilio/transfer-answer     — TwiML for human agent pickup
  POST /api/twilio/transfer-keypress   — TwiML for DTMF accept/decline
"""
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from livekit.api import (
    AccessToken,
    RoomAgentDispatch,
    RoomConfiguration,
    VideoGrants,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import Appointment, CallSession
from database.session import AsyncSessionLocal, create_tables, get_db
from services.transfer import (
    build_conference_twiml,
    build_decline_twiml,
    build_transfer_answer_twiml,
)
from tools.appointment import get_appointments_impl

logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup (dev convenience — use Alembic in prod)."""
    await create_tables()
    logger.info("Voice Agent API ready on port %s", settings.port)
    yield
    logger.info("Voice Agent API shutting down")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Voice Agent API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response schemas ─────────────────────────────────────────────────

class TokenRequest(BaseModel):
    """Body for POST /api/token."""
    room_name: str
    participant_name: str
    role: Literal["caller", "watcher"]


class TokenResponse(BaseModel):
    """Response for POST /api/token."""
    token: str
    livekit_url: str
    room_name: str
    identity: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    """Liveness probe — always returns 200."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/token", response_model=TokenResponse)
async def generate_token(body: TokenRequest) -> TokenResponse:
    """
    Generate a LiveKit access token for a caller or watcher.

    Callers receive full publish + subscribe grants (audio only).
    Watchers receive subscribe-only + data send grants (no audio publish).
    """
    identity = f"{body.role}-{uuid.uuid4().hex[:8]}"

    is_caller = body.role == "caller"

    grants = VideoGrants(
        room_join=True,
        room=body.room_name,
        can_publish=is_caller,
        can_publish_data=True,       # both roles can send data channel messages
        can_subscribe=True,
        can_publish_sources=["microphone"] if is_caller else [],
    )

    builder = (
        AccessToken(api_key=settings.livekit_api_key, api_secret=settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(body.participant_name)
        .with_grants(grants)
    )

    # Explicitly dispatch the voice agent into the caller's room. The dispatch
    # only fires when the room is *created*, so we also set a short empty_timeout:
    # otherwise the room lingers (LiveKit Cloud default 300s) and a rejoin lands
    # in the existing room → no dispatch → caller stuck on "connecting".
    # empty_timeout=10s means the room closes ~10s after the last participant
    # leaves, so each fresh join recreates the room and re-triggers dispatch.
    # Only the caller token carries the dispatch (avoids a duplicate request
    # when a watcher also joins).
    if is_caller:
        builder = builder.with_room_config(
            RoomConfiguration(
                agents=[RoomAgentDispatch(agent_name="voice-agent")],
                empty_timeout=10,
            )
        )

    token = builder.to_jwt()

    logger.info("Token issued: room=%s identity=%s role=%s", body.room_name, identity, body.role)

    return TokenResponse(
        token=token,
        livekit_url=settings.livekit_url,
        room_name=body.room_name,
        identity=identity,
    )


@app.post("/api/webhook")
async def livekit_webhook(request: Request) -> dict:
    """
    Receive and log LiveKit webhook events.

    LiveKit posts events for room lifecycle and participant changes.
    We upsert CallSession rows so the dashboard can query call history.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    event = body.get("event", "unknown")
    room_info = body.get("room", {})
    participant = body.get("participant", {})
    room_name = room_info.get("name", "")

    logger.info(
        "LiveKit webhook: event=%s room=%s participant=%s",
        event,
        room_name,
        participant.get("identity", ""),
    )

    async with AsyncSessionLocal() as db:
        if event == "room_started" and room_name:
            existing = await db.execute(
                select(CallSession).where(CallSession.room_name == room_name)
            )
            if not existing.scalars().first():
                db.add(CallSession(room_name=room_name, status="active"))
                await db.commit()
                logger.info("CallSession created for room: %s", room_name)

        elif event == "room_finished" and room_name:
            result = await db.execute(
                select(CallSession).where(CallSession.room_name == room_name)
            )
            session = result.scalars().first()
            if session:
                session.status = "ended"
                session.ended_at = datetime.now(timezone.utc)
                await db.commit()
                logger.info("CallSession ended for room: %s", room_name)

        elif event == "participant_joined":
            logger.info(
                "Participant joined: %s in room %s",
                participant.get("identity"),
                room_name,
            )

        elif event == "participant_left":
            logger.info(
                "Participant left: %s from room %s",
                participant.get("identity"),
                room_name,
            )

    return {"status": "ok"}


@app.get("/api/appointments")
async def list_appointments() -> list[dict]:
    """Return all appointments ordered by creation date descending."""
    return await get_appointments_impl(limit=100)


@app.get("/api/appointments/{appointment_id}")
async def get_appointment(appointment_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Return a single appointment by UUID, or 404 if not found."""
    try:
        appt_uuid = uuid.UUID(appointment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")

    result = await db.execute(
        select(Appointment).where(Appointment.id == appt_uuid)
    )
    appt = result.scalars().first()

    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    return {
        "id": str(appt.id),
        "confirmation_number": appt.confirmation_number,
        "name": appt.name,
        "reason": appt.reason,
        "date": appt.date.isoformat(),
        "time_slot": appt.time_slot,
        "phone": appt.phone,
        "status": appt.status,
        "room_name": appt.room_name,
        "created_at": appt.created_at.isoformat(),
    }


@app.get("/api/calls")
async def list_calls(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Return recent call sessions for the monitoring dashboard."""
    result = await db.execute(
        select(CallSession).order_by(CallSession.started_at.desc()).limit(50)
    )
    sessions = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "room_name": s.room_name,
            "status": s.status,
            "summary": s.summary,
            "started_at": s.started_at.isoformat(),
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        }
        for s in sessions
    ]


# ── Twilio TwiML webhooks ─────────────────────────────────────────────────────

@app.post("/api/twilio/transfer-answer")
async def twilio_transfer_answer(request: Request) -> Response:
    """
    TwiML webhook called when the human agent picks up the transfer call.

    Speaks a summary of the call and prompts for DTMF accept (1) or decline (2).
    Query params: caller_name, reason, room_name.
    """
    params = dict(request.query_params)
    caller_name = params.get("caller_name", "a caller")
    reason = params.get("reason", "an enquiry")
    room_name = params.get("room_name", "")

    # conference_name is room_name — used to bridge caller into same conference
    twiml = build_transfer_answer_twiml(
        caller_name=caller_name,
        reason=reason,
        conference_name=room_name,
    )
    logger.info("TwiML transfer-answer: caller=%s reason=%s", caller_name, reason)
    return Response(content=twiml, media_type="application/xml")


@app.post("/api/twilio/transfer-keypress")
async def twilio_transfer_keypress(request: Request) -> Response:
    """
    TwiML webhook called after the human agent presses a DTMF key.

    Digit 1 → accept: put human into conference room.
    Digit 2 (or anything else) → decline: hang up and agent resumes.
    Query params: conference, caller.
    """
    params = dict(request.query_params)
    form = await request.form()

    digit = str(form.get("Digits", "2"))
    conference_name = params.get("conference", "default-room")
    caller_name = params.get("caller", "the caller")

    if digit == "1":
        logger.info("Transfer ACCEPTED by human agent for conference=%s", conference_name)
        twiml = build_conference_twiml(conference_name)
    else:
        logger.info("Transfer DECLINED by human agent (digit=%s)", digit)
        twiml = build_decline_twiml()

    return Response(content=twiml, media_type="application/xml")


# ── Dev runner ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.is_development,
        log_level=settings.log_level.lower(),
    )
