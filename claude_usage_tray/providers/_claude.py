"""claude_usage_tray.providers._claude -- Anthropic / Claude Code.

Move-only extraction of what `api.py` used to hardcode: the credentials
shape, the OAuth usage endpoint, the `claude-code/<version>` User-Agent, the
`claude update` refresh, and `_merge_scoped_limits` (which folds Anthropic's
per-model `limits` array into the flat `five_hour`/`seven_day` fields the
rest of the app reads). None of that behavior changed -- it just stopped
being the only possibility.

The macOS Keychain fallback in `_oauth_blob` moved here for the same reason:
it is Claude Code's Keychain entry, under Claude Code's fixed service name,
and no other provider has one. Reading it from `api.py` would have made a
provider-agnostic module carry one provider's storage quirk -- and, worse,
would only have fixed `read_access_token`, leaving `usage_request` (which
reads the credentials again to build the Authorization header) still looking
for a file macOS never wrote.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .. import platform
from ._types import UsageRequest, quota_entry

API_URL_USAGE = "https://api.anthropic.com/api/oauth/usage"
FALLBACK_USER_AGENT = "claude-code/2.1.201"
CREDENTIALS_FILENAME = ".credentials.json"
#: The Keychain service name Claude Code stores its credentials JSON under on
#: macOS. Fixed by Claude Code, not by us -- it takes no config-dir suffix,
#: which is why only the ambient default directory can read it.
KEYCHAIN_SERVICE = "Claude Code-credentials"


def _cli_path() -> Path | None:
    import shutil

    found = shutil.which("claude")
    if found:
        path = Path(found)
        if path.suffix.lower() == ".ps1":
            for ext in (".cmd", ".exe"):
                alt = path.with_suffix(ext)
                if alt.is_file():
                    return alt
        return path

    appdata = os.environ.get("APPDATA")
    if appdata:
        for name in ("claude.cmd", "claude.exe"):
            candidate = Path(appdata) / "npm" / name
            if candidate.is_file():
                return candidate
    return None


def _cli_version() -> str:
    cli = _cli_path()
    if not cli or not cli.is_file():
        return FALLBACK_USER_AGENT.removeprefix("claude-code/")
    try:
        proc = subprocess.run(
            [str(cli), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=platform.subprocess_flags(),
        )
        match = re.match(r"(\d+\.\d+\.\d+)", proc.stdout.strip())
        return match.group(1) if match else FALLBACK_USER_AGENT.removeprefix("claude-code/")
    except Exception:
        return FALLBACK_USER_AGENT.removeprefix("claude-code/")


def _default_config_dir() -> Path:
    # `CLAUDE_CONFIG_DIR` is exported by Claude Code and inherited by the
    # processes it spawns, which is what lets the MCP server and the hooks
    # report on the account that launched them with no configuration.
    #
    # Module-level rather than only a method because `_is_ambient` below runs
    # before any adapter instance is in hand, and one definition is what keeps
    # "the ambient directory" from meaning two things in one file.
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser()
    return Path.home() / ".claude"


def _is_ambient(config_dir: Path) -> bool:
    """Whether `config_dir` is the directory the local Claude Code administers.

    The same question `config.ClaudeProfile.is_ambient_default` answers, asked
    of a bare path because an adapter never sees a profile. Two callers below
    need it -- the Keychain fallback and `refresh_credentials` -- for the same
    underlying reason: both reach for state belonging to whichever account the
    local CLI is logged into, and neither can tell one config directory's
    account from another's.
    """
    default = _default_config_dir()
    try:
        return config_dir.expanduser().resolve() == default.expanduser().resolve()
    except OSError:
        return str(config_dir).lower() == str(default).lower()


def _read_credentials_blob(config_dir: Path) -> str | None:
    """The raw credentials JSON for `config_dir`, from wherever this OS keeps it.

    The file comes first and always wins: it is per-directory, so a user who
    points a profile at a copied `.claude` gets that profile's token, and a
    WSL directory mounted on a Mac keeps working.

    The Keychain is the fallback, and only for the ambient default directory.
    Claude Code on macOS writes no `.credentials.json` at all -- the blob
    lives in the login Keychain under a single, fixed service name, with no
    room for a config-dir qualifier. So it can only answer for the one
    directory that *is* the ambient default; letting any other profile fall
    back to it would make every macOS profile silently report the default
    account's usage, which is worse than reporting no session at all.

    An `OSError` on a file that *exists* is a real fault, not "nothing here,
    try the store": falling through would answer one profile with another
    account's token.
    """
    path = config_dir / CREDENTIALS_FILENAME
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    if _is_ambient(config_dir):
        return platform.read_secret(KEYCHAIN_SERVICE)
    return None


def _oauth_blob(config_dir: Path) -> dict[str, Any]:
    raw = _read_credentials_blob(config_dir)
    if not raw:
        return {}
    try:
        creds = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(creds, dict):
        # A credentials file holding a bare list would blow up on `.get`.
        return {}
    blob = creds.get("claudeAiOauth")
    return blob if isinstance(blob, dict) else {}


def _model_slug(display_name: str) -> str:
    cleaned = "".join(char if char.isalnum() else " " for char in display_name.lower())
    return "_".join(cleaned.split())


def _merge_scoped_limits(data: dict[str, Any]) -> dict[str, Any]:
    """Fold Anthropic's per-model `limits` entries into flat fields.

    Verbatim from the pre-adapter `api.py`; the only change is where it
    lives. It is Anthropic-shaped through and through (`group`, `scope.model`,
    `percent`), which is precisely why it belongs to this adapter.
    """
    limits = data.get("limits")
    if not isinstance(limits, list):
        return data

    reset_to_field: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict) and value.get("utilization") is not None:
            resets_at = value.get("resets_at")
            if resets_at:
                reset_to_field.setdefault(resets_at, key)

    group_prefix: dict[str, str] = {}
    for limit in limits:
        if not isinstance(limit, dict) or limit.get("scope"):
            continue
        group = limit.get("group")
        resets_at = limit.get("resets_at")
        if group and resets_at and resets_at in reset_to_field:
            group_prefix.setdefault(group, reset_to_field[resets_at])

    merged = dict(data)
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        model = (limit.get("scope") or {}).get("model") or {}
        display_name = model.get("display_name")
        prefix = group_prefix.get(limit.get("group"))
        if not display_name or not prefix:
            continue
        field = f"{prefix}_{_model_slug(display_name)}"
        if merged.get(field) is not None:
            continue
        merged[field] = {
            "utilization": float(limit.get("percent") or 0),
            "resets_at": limit.get("resets_at"),
        }
    return merged


def _quota(data: dict[str, Any], field: str) -> dict[str, Any] | None:
    entry = data.get(field)
    if not isinstance(entry, dict) or entry.get("utilization") is None:
        return None
    return quota_entry(entry.get("utilization"), entry.get("resets_at"))


class ClaudeProvider:
    id = "claude"
    display_name = "Claude Code"
    credentials_filename = CREDENTIALS_FILENAME
    windows = ("session", "weekly", "weekly_fable")
    config_dir_label = "Carpeta de Claude"
    config_dir_hint = (
        "Elegi la carpeta donde Claude guarda tu sesion.\n"
        "Suele verse como: C:/Users/TU_USUARIO/.claude"
    )

    def default_config_dir(self) -> Path:
        return _default_config_dir()

    def account_label(self, config_dir: Path) -> str | None:
        oauth = _oauth_blob(config_dir)
        for key in ("email", "username", "name", "displayName"):
            value = oauth.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def access_token(self, config_dir: Path) -> str | None:
        token = _oauth_blob(config_dir).get("accessToken")
        return token if isinstance(token, str) and token else None

    def usage_request(self, config_dir: Path) -> UsageRequest | None:
        token = self.access_token(config_dir)
        if not token:
            return None
        return UsageRequest(
            url=API_URL_USAGE,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": f"claude-code/{_cli_version()}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )

    def normalize(self, raw: Any) -> dict[str, Any]:
        data = _merge_scoped_limits(raw) if isinstance(raw, dict) else {}
        return {
            "session": _quota(data, "five_hour"),
            "weekly": _quota(data, "seven_day"),
            "weekly_fable": _quota(data, "seven_day_fable"),
            "raw": data,
        }

    def refresh_credentials(self, config_dir: Path) -> bool:
        """`claude update` renews the stored OAuth token as a side effect.

        Only attempted for the ambient default directory. The pre-adapter
        `api.py` gated this on `ClaudeProfile.supports_refresh`, which was
        exactly this comparison; keeping the check *inside* the adapter is
        what lets `api.py` stop knowing about it. A profile pointing at some
        other `.claude` folder (a second account, a WSL path) is not the
        session the local CLI would refresh, so running it there would renew
        the wrong account's token and report success for it.
        """
        if not _is_ambient(config_dir):
            return False
        cli = _cli_path()
        if not cli or not cli.is_file():
            return False
        try:
            proc = subprocess.run(
                [str(cli), "update"],
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=platform.subprocess_flags(),
            )
            return proc.returncode == 0
        except Exception:
            return False


PROVIDER = ClaudeProvider()

__all__ = ["PROVIDER", "ClaudeProvider"]
