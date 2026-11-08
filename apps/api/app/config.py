from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEXUSINTEL_", env_file=".env", extra="ignore")
    env: str = "development"
    database_url: str = "sqlite:///./nexusintel.db"
    session_secret: str = "local-only-change-me"
    demo_password: str = "change-me-locally"
    bootstrap_secret: str = ""
    cors_origins: str = "http://localhost:3000"
    session_hours: int = 8
    news_poll_interval_seconds: int = 900
    news_request_timeout_seconds: int = 20
    news_max_items_per_sync: int = 50
    news_max_feed_bytes: int = 2_000_000
    evidence_dir: str = "./data/evidence"
    evidence_max_bytes: int = 25_000_000
    zero_cost_mode: bool = True
    ollama_base_url: str = "http://ollama:11434"
    ollama_api_key: str = ""
    ollama_request_timeout_seconds: int = 60
    ollama_default_model: str = ""
    ollama_context_limit: int = 4096
    openai_enabled: bool = False
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-5-mini"
    openai_spending_limit_usd: float = 0.0
    openai_cost_per_1k_tokens_usd: float = 0.0

settings = Settings()
