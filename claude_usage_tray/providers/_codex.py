"""claude_usage_tray.providers._codex -- OpenAI Codex CLI.

Codex stores a ChatGPT OAuth session in `~/.codex/auth.json` and exposes
quota at `GET /backend-api/codex/usage`, which reports the same two things
Anthropic does -- a percentage used and a reset instant -- for a 5-hour and
a 7-day window. That correspondence is why Codex needs no new quota window
ids: `primary_window` *is* `session` and `secondary_window` *is* `weekly`,
so every existing consumer (tooltip, popup, alerts, budget, MCP report)
renders a Codex profile with no change at all. `weekly_fable` is simply
`None`, the same way it already is for a Claude account without Fable.

Two shape differences are absorbed here rather than leaked downstream:

* resets arrive as **unix epoch seconds**, not ISO-8601 (`epoch_to_iso`);
* the account id must be echoed back in a `chatgpt-account-id` header.

**On refresh.** Unlike Claude there is no `codex update` that renews a token
as a side effect, so this adapter performs the OAuth refresh itself, with
`urllib` rather than `requests` -- this package must stay importable without
the network stack (`tests/test_import_hygiene.py`). Codex access tokens last
about ten days and the Codex CLI refreshes them on its own whenever it runs,
so this path is a fallback for a tray left running across an expiry, not the
normal case.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ._types import UsageRequest, epoch_to_iso, quota_entry

API_URL_USAGE = "https://chatgpt.com/backend-api/codex/usage"
TOKEN_URL = "https://auth.openai.com/oauth/token"

#: The Codex CLI's public OAuth client id. Not a secret -- it is the `aud`
#: claim of every id_token the CLI stores, and a public client by design.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

CREDENTIALS_FILENAME = "auth.json"
USER_AGENT = "codex-cli"


def _auth_blob(config_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads((config_dir / CREDENTIALS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _tokens(config_dir: Path) -> dict[str, Any]:
    blob = _auth_blob(config_dir).get("tokens")
    return blob if isinstance(blob, dict) else {}


def _id_token_claims(config_dir: Path) -> dict[str, Any]:
    """Decode the stored id_token's payload without verifying it.

    Unverified on purpose and safe here: the only thing read out of it is the
    account's email, to label a profile the user already pointed us at. No
    authorization decision is made from these claims -- the access token is
    what the API validates, and it validates it server-side.
    """
    import base64

    token = _tokens(config_dir).get("id_token")
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def _window(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    return quota_entry(entry.get("used_percent"), epoch_to_iso(entry.get("reset_at")))


class CodexProvider:
    id = "codex"
    display_name = "OpenAI Codex"
    credentials_filename = CREDENTIALS_FILENAME
    # No Fable equivalent; the third window stays absent rather than empty.
    windows = ("session", "weekly")
    config_dir_label = "Carpeta de Codex"
    config_dir_hint = (
        "Elegi la carpeta donde Codex guarda tu sesion.\n"
        "Suele verse como: C:/Users/TU_USUARIO/.codex"
    )

    def default_config_dir(self) -> Path:
        if os.environ.get("CODEX_HOME"):
            return Path(os.environ["CODEX_HOME"]).expanduser()
        return Path.home() / ".codex"

    def account_label(self, config_dir: Path) -> str | None:
        email = _id_token_claims(config_dir).get("email")
        return email.strip() if isinstance(email, str) and email.strip() else None

    def access_token(self, config_dir: Path) -> str | None:
        token = _tokens(config_dir).get("access_token")
        return token if isinstance(token, str) and token else None

    def usage_request(self, config_dir: Path) -> UsageRequest | None:
        token = self.access_token(config_dir)
        if not token:
            return None
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        account_id = _tokens(config_dir).get("account_id")
        if isinstance(account_id, str) and account_id:
            headers["chatgpt-account-id"] = account_id
        return UsageRequest(url=API_URL_USAGE, headers=headers)

    def normalize(self, raw: Any) -> dict[str, Any]:
        data = raw if isinstance(raw, dict) else {}
        rate_limit = data.get("rate_limit")
        rate_limit = rate_limit if isinstance(rate_limit, dict) else {}
        return {
            "session": _window(rate_limit.get("primary_window")),
            "weekly": _window(rate_limit.get("secondary_window")),
            "weekly_fable": None,
            "raw": data,
        }

    def refresh_credentials(self, config_dir: Path) -> bool:
        """Exchange the stored refresh token for a new access token.

        Writes the result back into `auth.json` in place, preserving every
        key the Codex CLI owns and touching only the three it rotates. A
        failure of any kind returns `False`: `api.py` then surfaces the
        ordinary "session expired" message, which is recoverable by opening
        Codex, exactly as it is for Claude.
        """
        import urllib.error
        import urllib.request

        blob = _auth_blob(config_dir)
        tokens = blob.get("tokens")
        if not isinstance(tokens, dict):
            return False
        refresh = tokens.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            return False

        body = json.dumps(
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "scope": "openid profile email",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict) or not payload.get("access_token"):
            return False

        updated = dict(tokens)
        for source, target in (
            ("access_token", "access_token"),
            ("id_token", "id_token"),
            ("refresh_token", "refresh_token"),
        ):
            value = payload.get(source)
            if isinstance(value, str) and value:
                updated[target] = value

        new_blob = dict(blob)
        new_blob["tokens"] = updated
        try:
            # Written through a temp file and renamed, so a crash mid-write
            # cannot leave the user's Codex session truncated and unusable.
            path = config_dir / CREDENTIALS_FILENAME
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(new_blob, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            return False
        return True


PROVIDER = CodexProvider()

__all__ = ["PROVIDER", "CodexProvider"]
