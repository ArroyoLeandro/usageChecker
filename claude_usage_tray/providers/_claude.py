"""claude_usage_tray.providers._claude -- Anthropic / Claude Code.

Move-only extraction of what `api.py` used to hardcode: the credentials
shape, the OAuth usage endpoint, the `claude-code/<version>` User-Agent, the
`claude update` refresh, and `_merge_scoped_limits` (which folds Anthropic's
per-model `limits` array into the flat `five_hour`/`seven_day` fields the
rest of the app reads). None of that behavior changed -- it just stopped
being the only possibility.

The macOS Keychain fallback in `_oauth_blob` moved here for the same reason:
it is Claude Code's Keychain entry, under a service name Claude Code derives
from the config directory, and no other provider has one. Reading it from
`api.py` would have made a provider-agnostic module carry one provider's
storage quirk -- and, worse, would only have fixed `read_access_token`,
leaving `usage_request` (which reads the credentials again to build the
Authorization header) still looking for a file macOS never wrote.
"""

from __future__ import annotations

import hashlib
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
#: Base of the Keychain service name Claude Code stores its credentials JSON
#: under on macOS. Every directory other than the home default appends
#: `-<digest>` to it; see `_keychain_services`.
KEYCHAIN_SERVICE = "Claude Code-credentials"
#: How many hex characters of the config directory's SHA-256 Claude Code puts
#: in the qualified service name.
KEYCHAIN_DIGEST_CHARS = 8
#: The config directory Claude Code treats as its default, and the only one
#: whose Keychain entry carries no digest suffix. Deliberately *not*
#: `_default_config_dir()`: that one follows `CLAUDE_CONFIG_DIR`, and the
#: unsuffixed entry belongs to `~/.claude` whatever the environment says.
HOME_DEFAULT_DIRNAME = ".claude"


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
    of a bare path because an adapter never sees a profile. `refresh_credentials`
    is the one caller: `claude update` renews the session of whichever account
    the local CLI is logged into, and it takes no config directory, so it can
    only be run for the directory that *is* the local CLI's.

    Reading credentials no longer asks this. The Keychain names its entries per
    config directory (`_keychain_services`), so any profile can be told apart
    from any other -- which is what the fallback needed and this question could
    not give it.
    """
    default = _default_config_dir()
    try:
        return config_dir.expanduser().resolve() == default.expanduser().resolve()
    except OSError:
        return str(config_dir).lower() == str(default).lower()


def _is_home_default(config_dir: Path) -> bool:
    """Whether `config_dir` is the `~/.claude` Claude Code falls back to.

    Distinct from `_is_ambient`, which follows `CLAUDE_CONFIG_DIR`. The
    unsuffixed Keychain entry is written for `~/.claude` and stays that
    account's whatever the environment of *this* process happens to say, so
    reusing `_is_ambient` here would hand the home account's token to whichever
    directory the launching shell pointed at -- and, worse, would stop
    answering for `~/.claude` itself.
    """
    home_default = Path.home() / HOME_DEFAULT_DIRNAME
    try:
        return config_dir.expanduser().resolve() == home_default.resolve()
    except OSError:
        return str(config_dir).lower() == str(home_default).lower()


def _keychain_services(config_dir: Path) -> list[str]:
    """Keychain service names that could hold `config_dir`'s credentials, in
    the order they should be tried.

    Claude Code derives the name from the config directory: the first
    `KEYCHAIN_DIGEST_CHARS` hex characters of the SHA-256 of the directory
    path, appended to `KEYCHAIN_SERVICE`. The one exception is its own default
    `~/.claude`, whose entry carries no suffix at all -- so a rule that
    suffixed everything would break the only account that works today.

    Hence two candidates for the home default and exactly one for anybody else.
    The qualified name goes first even for the home default: it names a single
    directory, so preferring it can only ever be more precise, and it keeps
    working if Claude Code ever starts suffixing that entry too. The unsuffixed
    name is never offered to another directory, because that is the lookup that
    would report the home account's usage under a second profile's name.

    Both the given spelling and the resolved one are hashed, since a profile
    may store `~/.claude-work` or a path through a symlinked home while Claude
    Code hashed the real one.
    """
    spellings: list[str] = []
    expanded = config_dir.expanduser()
    spellings.append(str(expanded))
    try:
        resolved = str(expanded.resolve())
    except OSError:
        resolved = None
    if resolved is not None and resolved not in spellings:
        spellings.append(resolved)

    services: list[str] = []
    for spelling in spellings:
        digest = hashlib.sha256(spelling.encode("utf-8")).hexdigest()[:KEYCHAIN_DIGEST_CHARS]
        service = f"{KEYCHAIN_SERVICE}-{digest}"
        if service not in services:
            services.append(service)
    if _is_home_default(config_dir):
        services.append(KEYCHAIN_SERVICE)
    return services


def _read_credentials_blob(config_dir: Path) -> str | None:
    """The raw credentials JSON for `config_dir`, from wherever this OS keeps it.

    The file comes first and always wins: it is per-directory, so a user who
    points a profile at a copied `.claude` gets that profile's token, and a
    WSL directory mounted on a Mac keeps working.

    The Keychain is the fallback. Claude Code on macOS writes no
    `.credentials.json` at all -- the blob lives in the login Keychain, under a
    service name derived from the config directory (`_keychain_services`).
    Because the name identifies the directory, every profile can be answered
    from the store without any risk of handing one account's token to another;
    a directory nobody ever logged in from simply has no entry and reads as no
    session.

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
    for service in _keychain_services(config_dir):
        blob = platform.read_secret(service)
        if blob:
            return blob
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
