"""
Reset the LiveKit room — clears stale/zombie participants (dead agents or ghost
callers) that pin a room open and block fresh agent dispatch ("stuck on
connecting"). Deletes the room entirely, so the next caller join recreates it
fresh and re-triggers explicit agent dispatch.

Usage:
  PYTHONPATH=backend python backend/scripts/reset_room.py            # resets "main-room"
  PYTHONPATH=backend python backend/scripts/reset_room.py some-room  # resets a named room

Safe to run anytime no one is on a live call. It will disconnect anyone currently
in the room, so don't run it mid-call unless you intend to reset.
"""
import asyncio
import sys

from livekit import api

from config import settings

ROOM = sys.argv[1] if len(sys.argv) > 1 else "main-room"


async def main() -> None:
    """List the room's participants, then delete the room to clear it."""
    lk = api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        try:
            parts = await lk.room.list_participants(
                api.ListParticipantsRequest(room=ROOM)
            )
            if parts.participants:
                print(f"Participants in {ROOM!r} before reset:")
                for p in parts.participants:
                    kind = api.ParticipantInfo.Kind.Name(p.kind).replace(
                        "PARTICIPANT_KIND_", ""
                    )
                    print(f"  - {p.identity} [{kind}]")
            else:
                print(f"{ROOM!r} is empty.")
        except Exception:
            print(f"{ROOM!r} does not exist (already clear).")
            return

        await lk.room.delete_room(api.DeleteRoomRequest(room=ROOM))
        print(f"Deleted room {ROOM!r}. Next join will recreate it fresh.")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
