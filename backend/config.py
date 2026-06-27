"""
Application configuration — all env vars loaded here, nowhere else.
Import `settings` from this module wherever config is needed.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings loaded from .env / environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LiveKit
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    # Outbound SIP trunk id (from scripts/setup_sip_trunk.py). Used to dial the
    # human agent into the LiveKit room for a real warm-transfer audio bridge.
    livekit_sip_trunk_id: str = ""

    # LLM
    # Anthropic (Claude) is preferred when ANTHROPIC_API_KEY is set — reliable
    # tool-calling and low latency on Haiku. Falls back to Groq/OpenAI otherwise.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    groq_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    llm_base_url: str = "https://api.groq.com/openai/v1"

    @property
    def use_anthropic(self) -> bool:
        """True when a Claude (Anthropic) key is configured — preferred LLM path."""
        return bool(self.anthropic_api_key)

    # Deepgram — this project's key only has aura-1 access (aura-2 → 403)
    deepgram_api_key: str
    deepgram_stt_model: str = "nova-2"
    deepgram_tts_voice: str = "aura-asteria-en"

    # Database
    database_url: str = "postgresql+asyncpg://voice_agent:voice_agent_dev@localhost:5432/voice_agent"

    # Twilio (REST — outbound PSTN call + TwiML accept/decline)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_human_agent_number: str = ""

    # Twilio Elastic SIP Trunk (termination) — used to register the LiveKit
    # outbound trunk so LiveKit can dial the human into the room over SIP.
    twilio_sip_termination_uri: str = ""   # e.g. your-trunk.pstn.twilio.com
    twilio_sip_username: str = ""          # SIP credential-list username
    twilio_sip_password: str = ""          # SIP credential-list password

    # App
    public_base_url: str = "http://localhost:8000"
    app_env: str = "development"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def llm_api_key(self) -> str:
        """Return Groq key if set, otherwise fall back to OpenAI."""
        return self.groq_api_key or self.openai_api_key

    @property
    def is_development(self) -> bool:
        """True when running in local dev mode."""
        return self.app_env == "development"


settings = Settings()
