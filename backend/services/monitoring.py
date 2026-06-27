"""
Real-time monitoring event publisher.

All components call publish_event() to push typed JSON payloads to the
LiveKit room data channel. The Next.js dashboard subscribes via useDataChannel.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

from livekit import rtc

logger = logging.getLogger(__name__)

# Data channel topic — frontend must subscribe to this exact topic
MONITOR_TOPIC = "agent-monitor"


async def publish_event(
    room: rtc.Room,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """
    Publish a typed monitoring event to the room data channel.

    event_type must be one of:
      transcript     | agent_state   | intent       |
      booking_update | call_status   | transfer_status
    """
    payload = {
        "type": event_type,
        "data": data,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await room.local_participant.publish_data(
            json.dumps(payload).encode("utf-8"),
            reliable=True,
            topic=MONITOR_TOPIC,
        )
    except Exception as exc:
        # Non-fatal — monitoring failure must not affect the call
        logger.warning("Failed to publish monitoring event %s: %s", event_type, exc)


async def publish_transcript(
    room: rtc.Room,
    role: str,
    text: str,
    final: bool = True,
) -> None:
    """Convenience wrapper for transcript events."""
    await publish_event(room, "transcript", {"role": role, "text": text, "final": final})


async def publish_agent_state(room: rtc.Room, state: str) -> None:
    """Convenience wrapper for agent state changes (listening/thinking/speaking)."""
    await publish_event(room, "agent_state", {"state": state})


async def publish_booking_update(room: rtc.Room, field: str, value: str) -> None:
    """Convenience wrapper for incremental booking data collection events."""
    await publish_event(room, "booking_update", {"field": field, "value": value})


async def publish_call_status(room: rtc.Room, status: str, **extra: Any) -> None:
    """Convenience wrapper for call lifecycle status changes."""
    await publish_event(room, "call_status", {"status": status, **extra})
