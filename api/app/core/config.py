"""Configuracion central (pydantic-settings). Todo secreto viene de variables de entorno."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Crimson API"
    env: str = Field("local", description="local | staging | production")
    debug: bool = False

    database_url: str = Field(
        "sqlite:///./crimson.db",
        description="Postgres en staging/produccion (postgresql+psycopg://...). SQLite solo para desarrollo rapido.",
    )
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = Field("solo-desarrollo-cambia-esto-en-.env-0123456789", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    cookie_secure: bool = False  # True en staging/produccion (HTTPS)
    cookie_domain: str | None = None

    # CORS: origenes del portal (nunca "*")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Enlaces publicos de pago
    public_base_url: str = "http://localhost:5173"
    payment_link_days: int = 30

    # Integraciones (vacias = adaptadores en modo manual/sandbox)
    onvo_public_key: str | None = None
    onvo_secret_key: str | None = None
    einvoice_provider: str = "none"  # none | alanube | gti

    @property
    def is_prod(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
