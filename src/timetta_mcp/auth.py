"""OAuth 2.0 support for Timetta: token storage, refresh, and browser login."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AUTH_URL = "https://auth.timetta.com"
DEFAULT_CLIENT_ID = "external"


def get_auth_url() -> str:
    return os.environ.get("TIMETTA_AUTH_URL", DEFAULT_AUTH_URL).rstrip("/")


def get_client_id() -> str:
    return os.environ.get("TIMETTA_CLIENT_ID", DEFAULT_CLIENT_ID)


def default_credentials_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "timetta-mcp" / "credentials.json"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "timetta-mcp" / "credentials.json"


def credentials_path() -> Path:
    env = os.environ.get("TIMETTA_CREDENTIALS_PATH")
    return Path(env) if env else default_credentials_path()


@dataclass
class StoredTokens:
    access_token: str
    refresh_token: str
    expires_at: float
    token_endpoint: str

    def __repr__(self) -> str:  # never leak token values
        return f"StoredTokens(expires_at={self.expires_at!r})"


class TokenStore:
    """Reads/writes the token file atomically; never leaks token values."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def __repr__(self) -> str:
        return f"TokenStore(path={str(self._path)!r})"

    def load(self) -> StoredTokens | None:
        """Return stored tokens, or None if the file does not exist.

        Raises ValueError if the file exists but is malformed.
        """
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return StoredTokens(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=float(data["expires_at"]),
                token_endpoint=data["token_endpoint"],
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise ValueError(f"Malformed credentials file {self._path}: {exc}") from exc

    def save(self, tokens: StoredTokens) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "token_endpoint": tokens.token_endpoint,
            }
        )
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass  # best-effort on Windows
