from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PUBLIC_ROUTES = [
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/runtime",
    "/api/v1/chaoxing/location/geocode",
    "/api/v1/chaoxing/location/search",
    "/api/v1/chaoxing/location/reverse-geocode",
    "/docs",
    "/openapi.json",
    "/",
    "/health",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # Deployment profile. "server" = existing multi-tenant Postgres deploy (default,
    # unchanged). "local" = single-user desktop app path.
    PROFILE: Literal["server", "local"] = "server"

    # Persistence backend. "postgres" = existing server DB (default). "sqlite" = local file.
    STORAGE_BACKEND: Literal["postgres", "sqlite"] = "postgres"

    # Local SQLite file path; required when STORAGE_BACKEND == "sqlite".
    SQLITE_PATH: str = ""

    # Path to the pre-built frontend dist bundle served by the desktop app.
    FRONTEND_DIST: str = ""

    # Database
    MAIN_DB_HOST: str = "localhost"
    MAIN_DB_NAME: str = "main_db"
    MAIN_DB_USER: str = ""
    MAIN_DB_PASSWORD: str = ""
    MAIN_DB_PORT: int = 5432

    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # CORS
    CORS_ORIGINS: list[str] = []

    # 定时任务 / 周期任务的「墙钟时间」按哪个时区解释。
    #
    # 用户填 08:00 意思是「我这里的早上 8 点」，不是「UTC 8 点」。容器默认跑在
    # UTC，若直接把这个 time 当成 UTC 用，北京时间用户会看到下次执行时间偏 8
    # 小时（08:00 → 显示 16:00）。存储与比较仍然统一用 UTC，只有「把用户输入的
    # HH:MM 落到哪一天哪个瞬间」这一步需要这个时区。
    #
    # 用 IANA 名称（Asia/Shanghai）而不是固定 +08:00，这样夏令时地区也能正确。
    SCHEDULE_TIMEZONE: str = "Asia/Shanghai"

    # Security
    ENFORCE_HTTPS: bool = True
    BCRYPT_ROUNDS: int = 12

    # Environment ("dev", "production", …) drives several runtime guards.
    ENV: str = "dev"

    # Docs / Swagger UI — default off; opt in via env for local dev
    DOCS_ENABLED: bool = False

    BAIDU_MAP_API_KEY: str | None = None

    # Optional bearer token guarding the /metrics endpoint. When set, requests
    # to /metrics must present `Authorization: Bearer <METRICS_TOKEN>`. When
    # unset (default), /metrics is open — acceptable only because the documented
    # nginx topology does not proxy /metrics externally. Set this in any
    # deployment where the uvicorn container is otherwise reachable.
    METRICS_TOKEN: str | None = None

    # Extra Host header values accepted besides localhost/127.0.0.1 and the
    # CORS_ORIGINS hosts, comma separated (e.g. "192.168.1.10,uh.lan,*.example.com").
    # Needed when the site is reached through a LAN IP or a second domain.
    ALLOWED_HOSTS: str = ""

    # Recreate the users table / tenant_template database at startup when the
    # Postgres init scripts did not run. Server profile only.
    DB_AUTO_BOOTSTRAP: bool = True
    # Directory holding 00-schema.sql and tenant_template.sql (auto-detected when empty).
    DB_BOOTSTRAP_SQL_DIR: str = ""

    # Administrators (server edition): comma-separated emails. When empty, the
    # first registered account (smallest users.id) is the administrator.
    ADMIN_EMAILS: str = ""

    # New-release notice for administrators. The server polls GitHub Releases;
    # set UPDATE_CHECK_ENABLED=false on air-gapped installs.
    #
    # This fork publishes tags but no GitHub releases, so the default URL answers
    # 404 and the notice never appears. That is the safe default: upstream's
    # release notes come with a `deploy_server.sh --tag` command that installs a
    # different image set (ghcr.io/sweetcornna) onto a different data volume
    # than this fork's deploy/compose.yml, so following it would stand up a
    # second, empty stack instead of updating the running one. Publish a release
    # on the fork repo to turn the notice on.
    UPDATE_CHECK_ENABLED: bool = True
    UPDATE_CHECK_INTERVAL_SECONDS: int = 6 * 3600
    UPDATE_CHECK_URL: str = "https://api.github.com/repos/dabai214109/university-helper/releases/latest"

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_present(cls, v: str) -> str:
        if not v or len(v) < 16:
            raise ValueError("SECRET_KEY must be set and at least 16 chars")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _cors_origins_present(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("CORS_ORIGINS must be set in environment variables")
        return v

    @model_validator(mode="after")
    def _postgres_creds_required(self) -> "Settings":
        if self.STORAGE_BACKEND == "postgres" and (not self.MAIN_DB_USER or not self.MAIN_DB_PASSWORD):
            raise ValueError("MAIN_DB_USER and MAIN_DB_PASSWORD must be set when STORAGE_BACKEND is 'postgres'")
        return self


settings = Settings()


def split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


# Opaque user id used by the single-user local profile. Handlers only need a
# <=64-char string; a constant is sufficient (see spec §3.5).
LOCAL_USER_ID = "local"
