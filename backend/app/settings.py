"""App settings — all secrets via .env (see .env.example at repo root)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    deepgram_api_key: str = ""

    # off | on — when off, agents use rule-only templated rationale (no LLM calls)
    agent_llm: str = "on"

    # Fast model for the hot agent loop (Groq) and quality model (Anthropic)
    groq_model: str = "llama-3.3-70b-versatile"
    anthropic_model: str = "claude-sonnet-4-6"
    # gpt-4o-mini accepts the max_tokens param used by both our chat fallback and
    # the Hive runtime; gpt-5* models reject it (require max_completion_tokens).
    openai_model: str = "gpt-4o-mini"

    db_path: str = "railmind.sqlite"
    sim_speed: float = 1.0  # sim-minutes per real second
    data_dir: str = "../data"


settings = Settings()
