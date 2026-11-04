from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEXUSINTEL_", env_file=".env", extra="ignore")
    env: str = "development"
    database_url: str = "sqlite:///./nexusintel.db"
    session_secret: str = "local-only-change-me"
    demo_password: str = "change-me-locally"
    cors_origins: str = "http://localhost:3000"
    session_hours: int = 8

settings = Settings()
