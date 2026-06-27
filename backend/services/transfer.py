"""
Warm transfer service — Twilio outbound call + TwiML bridge.

Flow:
  1. initiate_transfer() → Twilio REST creates call to human agent number
  2. Twilio hits /api/twilio/transfer-answer (TwiML webhook in main.py)
  3. TwiML plays summary, waits for DTMF (1=accept, 2=decline)
  4. /api/twilio/transfer-keypress handles the digit
     - Accept: bridge to Twilio conference (caller + human)
     - Decline: agent resumes conversation
"""
import logging
import urllib.parse
from xml.sax.saxutils import escape as xml_escape

from livekit import api
from twilio.rest import Client

from config import settings

logger = logging.getLogger(__name__)

# Identity given to the human agent when they join the LiveKit room over SIP.
HUMAN_AGENT_IDENTITY = "human-agent"


def _mask_phone(number: str) -> str:
    """Redact a phone number for logging — keep only the last 4 digits."""
    if not number:
        return "(unset)"
    return f"***{number[-4:]}" if len(number) >= 4 else "***"


def _get_twilio_client() -> Client:
    """Return an authenticated Twilio client."""
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


async def initiate_transfer(
    room_name: str,
    caller_name: str,
    transfer_reason: str,
) -> dict:
    """
    Warm-transfer entry point — dial the human agent.

    Prefers the real audio bridge (LiveKit SIP: dial the human into the SAME
    LiveKit room as the caller) when an outbound SIP trunk is configured. Falls
    back to the legacy Twilio REST + TwiML accept/decline flow (which only rings
    the human; it does NOT bridge the WebRTC caller) when it is not.
    """
    if settings.livekit_sip_trunk_id:
        return await dial_human_into_room(room_name, caller_name, transfer_reason)
    if settings.twilio_account_sid:
        logger.warning(
            "LIVEKIT_SIP_TRUNK_ID not set — using Twilio REST fallback "
            "(rings human, but does NOT bridge the caller's audio)."
        )
        return await _twilio_rest_transfer(room_name, caller_name, transfer_reason)
    logger.warning("Neither LiveKit SIP nor Twilio configured — skipping transfer")
    return {"status": "skipped", "reason": "transfer not configured"}


async def dial_human_into_room(
    room_name: str,
    caller_name: str,
    transfer_reason: str,
) -> dict:
    """
    Dial the human agent's phone into the LiveKit room via SIP.

    Creates a SIP participant on the configured outbound trunk. When the human
    answers, their phone audio enters `room_name` — the same room as the WebRTC
    caller — so the two can talk directly. This is the real warm-transfer bridge.
    """
    if not settings.twilio_human_agent_number:
        return {"status": "error", "error": "TWILIO_HUMAN_AGENT_NUMBER not set"}

    lk = api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        participant = await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=settings.livekit_sip_trunk_id,
                sip_call_to=settings.twilio_human_agent_number,
                room_name=room_name,
                participant_identity=HUMAN_AGENT_IDENTITY,
                participant_name="Human Agent",
                # Context for logs / the dashboard.
                participant_metadata=transfer_reason,
                krisp_enabled=True,        # noise-cancel the phone leg
                # Block until the human actually ANSWERS, so the agent's
                # participant_connected handoff fires on a real pickup — not on
                # ringing (which caused join→drop flapping + cut-off speech).
                # Raises on no-answer/failure, handled below.
                wait_until_answered=True,
            )
        )
        logger.info(
            "SIP human %s answered, joined room %s (participant=%s)",
            _mask_phone(settings.twilio_human_agent_number), room_name, participant.participant_id,
        )
        return {"status": "initiated", "participant_id": participant.participant_id}
    except Exception as exc:
        # No answer, busy, declined, or SIP/auth failure — log and report.
        logger.error("SIP call to human did not connect: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _twilio_rest_transfer(
    room_name: str,
    caller_name: str,
    transfer_reason: str,
) -> dict:
    """
    Legacy fallback: Twilio REST outbound call + TwiML accept/decline.

    NOTE: this only rings the human and plays a summary; it does NOT connect the
    WebRTC caller's audio (a Twilio conference can't reach a LiveKit participant).
    Configure LIVEKIT_SIP_TRUNK_ID to get the real bridge via dial_human_into_room.
    """
    params = urllib.parse.urlencode({
        "room_name": room_name,
        "caller_name": caller_name,
        "reason": transfer_reason,
    })
    webhook_url = f"{settings.public_base_url}/api/twilio/transfer-answer?{params}"

    try:
        client = _get_twilio_client()
        call = client.calls.create(
            to=settings.twilio_human_agent_number,
            from_=settings.twilio_phone_number,
            url=webhook_url,
            method="POST",
        )
        logger.info("Twilio transfer call created: %s → %s (SID: %s)",
                    _mask_phone(settings.twilio_phone_number),
                    _mask_phone(settings.twilio_human_agent_number), call.sid)
        return {"status": "initiated", "call_sid": call.sid}
    except Exception as exc:
        logger.error("Failed to initiate Twilio transfer: %s", exc)
        return {"status": "error", "error": str(exc)}


def build_transfer_answer_twiml(caller_name: str, reason: str, conference_name: str) -> str:
    """
    TwiML for the human agent's phone when they pick up.

    Speaks a summary and asks them to press 1 to accept or 2 to decline.
    """
    # URL-encode query params (caller_name may contain spaces/symbols → Twilio
    # error 11100 "Invalid URL" if interpolated raw), then XML-escape the '&'
    # that separates them so the attribute is valid XML.
    query = urllib.parse.urlencode(
        {"conference": conference_name, "caller": caller_name}
    )
    action = xml_escape(f"/api/twilio/transfer-keypress?{query}")
    # Spoken text must also be XML-escaped (& < > would break the document).
    safe_caller = xml_escape(caller_name)
    safe_reason = xml_escape(reason)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">
    Hello. This is an automated transfer from your voice assistant.
    You have a call from {safe_caller} regarding {safe_reason}.
    Press 1 to accept the call, or press 2 to decline.
  </Say>
  <Gather numDigits="1" action="{action}" method="POST" timeout="15">
    <Say voice="Polly.Joanna">Press 1 to accept, or 2 to decline.</Say>
  </Gather>
  <Say voice="Polly.Joanna">No input received. The call will not be transferred. Goodbye.</Say>
</Response>"""


def build_conference_twiml(conference_name: str) -> str:
    """TwiML that puts the human agent into a named Twilio conference room."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">Connecting you now. Please hold.</Say>
  <Dial>
    <Conference>{conference_name}</Conference>
  </Dial>
</Response>"""


def build_decline_twiml() -> str:
    """TwiML played when the human agent declines the transfer."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">Understood. Thank you. Goodbye.</Say>
  <Hangup/>
</Response>"""


async def add_caller_to_conference(caller_call_sid: str, conference_name: str) -> dict:
    """
    Move the caller's existing call into the Twilio conference.

    NOTE: This requires the caller to be on a Twilio PSTN call, not a WebRTC
    LiveKit call. For full audio bridging, the LiveKit room needs a SIP trunk
    configured pointing to Twilio. This is a placeholder for that flow.
    """
    # TODO: Implement SIP bridge from LiveKit room to Twilio conference
    # Requires: LiveKit SIP trunk + Twilio SIP domain configuration
    logger.warning("Caller-to-conference bridging not yet implemented (requires SIP trunk)")
    return {"status": "not_implemented"}
