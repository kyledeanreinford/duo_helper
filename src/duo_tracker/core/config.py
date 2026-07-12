import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class DuoAccount:
    """One Duolingo login we snapshot daily. Auth is a manually-harvested
    jwt_token cookie (no passwords stored anywhere); see .env.example for
    the harvest procedure."""
    person: str
    jwt: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/duo_tracker"

    # Comma-separated people to snapshot; each needs a DUO_JWT_<NAME> env var.
    # Per-person env vars (not a JSON blob) so re-harvesting one expired token
    # is a one-line .env edit, and each maps 1:1 onto a k8s Secret key later.
    duo_people: str = "kyle"

    # Where `duo-tracker probe` writes raw endpoint dumps.
    probe_dir: Path = Path("./data")

    # "Today" for snapshot_date is computed in this zone, NOT the system
    # clock's — the k8s container runs UTC, where 23:50 Chicago is already
    # tomorrow; naive date.today() files the whole day under the wrong date.
    timezone: str = "America/Chicago"

    def accounts(self) -> list[DuoAccount]:
        out: list[DuoAccount] = []
        for name in [p.strip().lower() for p in self.duo_people.split(",") if p.strip()]:
            jwt = os.environ.get(f"DUO_JWT_{name.upper()}") or _dotenv_lookup(f"DUO_JWT_{name.upper()}")
            if not jwt:
                raise RuntimeError(
                    f"No JWT configured for '{name}'. Log into duolingo.com as {name} "
                    f"in a browser, copy the 'jwt_token' cookie value (DevTools -> "
                    f"Application -> Cookies), and set DUO_JWT_{name.upper()} in .env."
                )
            out.append(DuoAccount(person=name, jwt=jwt))
        return out


def _dotenv_lookup(key: str) -> str | None:
    """DUO_JWT_* keys are dynamic, so they aren't Settings fields and
    pydantic-settings won't load them from .env for us. Read the file the
    same way (if present) so `.env` works without exporting vars."""
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
