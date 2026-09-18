"""Configuracion central (pydantic-settings). Todo secreto viene de variables de entorno."""

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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

    # Arranque inicial de entornos compartidos (ver app/bootstrap.py): invitacion de admin, nunca contrasena
    bootstrap_admin_email: str | None = None
    bootstrap_demo: bool = False
    # Cuentas de prueba: "correo:rol,correo:rol" (p. ej. "vendedor@ejemplo.com:ventas"). Se invita una vez por correo.
    bootstrap_invites: str | None = None

    @field_validator("database_url")
    @classmethod
    def _psycopg3(cls, v: str) -> str:
        # Railway/Heroku entregan postgres:// o postgresql://; SQLAlchemy necesita el driver psycopg 3 explicito
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @model_validator(mode="after")
    def _no_dev_secret_outside_local(self):
        if self.env != "local" and self.jwt_secret.startswith("solo-desarrollo"):
            raise ValueError("JWT_SECRET de desarrollo en un entorno compartido: definir uno propio (>= 32 caracteres)")
        if self.env != "local" and self.database_url.startswith("sqlite"):
            raise ValueError("Entornos compartidos requieren Postgres (DATABASE_URL)")
        return self

    @property
    def is_prod(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
