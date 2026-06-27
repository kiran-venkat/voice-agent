"""
One-time setup: register a LiveKit *outbound* SIP trunk that points at your
Twilio Elastic SIP Trunk's termination URI.

This trunk lets LiveKit dial a phone number (the human agent) into a LiveKit
room over SIP, so the WebRTC caller and the PSTN human end up in the same room
and can actually talk — the real warm-transfer audio bridge.

Prerequisites (Twilio Console → Elastic SIP Trunking):
  1. Create a SIP Trunk.
  2. Under Termination: set a Termination SIP URI, e.g. `my-trunk.pstn.twilio.com`.
  3. Under Termination → Authentication: add a Credential List (username + password).
     (LiveKit Cloud egress IPs vary, so credential auth is simpler than IP ACL.)
  4. Make sure the trunk has a phone number / the account can place outbound calls.
     On a trial account the destination number must be verified.

Then set these in .env:
  TWILIO_SIP_TERMINATION_URI=my-trunk.pstn.twilio.com
  TWILIO_SIP_USERNAME=<credential-list username>
  TWILIO_SIP_PASSWORD=<credential-list password>
  TWILIO_PHONE_NUMBER=+1...        # used as the outbound caller ID

Run:
  PYTHONPATH=backend python backend/scripts/setup_sip_trunk.py

It prints the new trunk id. Put it in .env as:
  LIVEKIT_SIP_TRUNK_ID=ST_xxxxxxxx
and restart the agent.
"""
import asyncio
import sys

from livekit import api

from config import settings


async def main() -> None:
    """Create (or report) the LiveKit outbound SIP trunk for Twilio."""
    missing = [
        name
        for name, value in {
            "TWILIO_SIP_TERMINATION_URI": settings.twilio_sip_termination_uri,
            "TWILIO_SIP_USERNAME": settings.twilio_sip_username,
            "TWILIO_SIP_PASSWORD": settings.twilio_sip_password,
            "TWILIO_PHONE_NUMBER": settings.twilio_phone_number,
        }.items()
        if not value
    ]
    if missing:
        print(f"ERROR: missing required .env values: {', '.join(missing)}")
        print("See the docstring at the top of this file for setup steps.")
        sys.exit(1)

    lk = api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    info = api.SIPOutboundTrunkInfo(
        name="twilio-warm-transfer",
        address=settings.twilio_sip_termination_uri,
        transport=api.SIPTransport.SIP_TRANSPORT_AUTO,
        numbers=[settings.twilio_phone_number],
        auth_username=settings.twilio_sip_username,
        auth_password=settings.twilio_sip_password,
    )
    try:
        # If a trunk with the same address exists, UPDATE it in place so the
        # current .env credentials are applied (keeps the same trunk id, so
        # LIVEKIT_SIP_TRUNK_ID stays valid). This is important when fixing a
        # Twilio "32202 bad user credentials" auth failure — re-run after
        # correcting TWILIO_SIP_USERNAME / TWILIO_SIP_PASSWORD.
        existing = await lk.sip.list_sip_outbound_trunk(
            api.ListSIPOutboundTrunkRequest()
        )
        for trunk in existing.items:
            if trunk.address == settings.twilio_sip_termination_uri:
                await lk.sip.update_sip_outbound_trunk(trunk.sip_trunk_id, info)
                print(f"Updated existing trunk {trunk.sip_trunk_id} with current "
                      f".env credentials (user={settings.twilio_sip_username!r}).")
                print(f"Keep LIVEKIT_SIP_TRUNK_ID={trunk.sip_trunk_id} in .env, "
                      f"then restart the agent.")
                return

        trunk = await lk.sip.create_sip_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(trunk=info)
        )
        print("Created LiveKit outbound SIP trunk.")
        print(f"  LIVEKIT_SIP_TRUNK_ID={trunk.sip_trunk_id}")
        print("Add that line to .env and restart the agent worker.")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
