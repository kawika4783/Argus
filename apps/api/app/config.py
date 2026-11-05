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

settings = Settings()
