from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _get_int(name: str) -> int | None:
    v = os.getenv(name)
    if v is None or not v.strip():
        return None
    return int(v.strip())


def _get_str(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v or default


def _parse_allowed_user(value: str | None) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    v = value.strip()
    if not v:
        return None, None
    if v.startswith("@"):
        username = v[1:].strip().lower()
        return None, username or None
    if v.isdigit():
        return int(v), None
    raise ValueError(
        "TELEGRAM_ALLOWED_USER_ID must be a numeric user id (recommended) or an @username"
    )


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    token: str

    allowed_user_id: int | None
    allowed_username: str | None
    claim_code: str | None

    state_path: Path
    max_turns: int

    agent: str
    agent_cmd: list[str] | None
    agent_workdir: Path
    agent_timeout_sec: int  # 0 => no timeout

    heartbeat_sec: int

    bg_heartbeat_sec: int

    inbox_dir: Path

    memory_dir: Path
    memory_enabled: bool
    memory_auto_rebuild: bool
    memory_max_snippets: int
    memory_snippet_chars: int

    stt_enabled: bool
    stt_model: str
    stt_language: str | None
    stt_prompt: str | None
    stt_max_chars: int
    stt_timeout_sec: int
    stt_keep_files: bool

    oauth_auto_peekaboo: bool
    oauth_auto_allow: bool
    oauth_browser_app: str

    codex_sandbox: str
    codex_model: str | None
    codex_extra_args: list[str]


def load_settings(base_dir: Path) -> Settings:
    load_dotenv(base_dir / ".env")

    token = _get_str("TELEGRAM_BOT_TOKEN") or _get_str("TG_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN (or TG_BOT_TOKEN)")

    allowed_user_id = None
    allowed_username = None
    try:
        allowed_user_id, allowed_username = _parse_allowed_user(
            _get_str("TELEGRAM_ALLOWED_USER_ID") or _get_str("TG_ALLOWED_USER_ID")
        )
    except ValueError as e:
        raise SystemExit(str(e))

    if not allowed_username:
        allowed_username = (_get_str("TELEGRAM_ALLOWED_USERNAME") or "").lstrip("@").strip().lower() or None

    claim_code = _get_str("TELEGRAM_CLAIM_CODE")

    if allowed_user_id is None and allowed_username is None and not claim_code:
        raise SystemExit(
            "Set TELEGRAM_ALLOWED_USER_ID, or set TELEGRAM_CLAIM_CODE for /claim onboarding"
        )

    state_path = Path(_get_str("STATE_PATH", str(base_dir / "data" / "state.json"))).expanduser()
    max_turns = int(_get_str("MAX_TURNS", "20"))

    agent = (_get_str("AGENT", "codex") or "codex").lower()
    agent_timeout_sec = int(_get_str("AGENT_TIMEOUT_SEC", "0"))
    agent_workdir = Path(_get_str("AGENT_WORKDIR", os.getcwd())).expanduser()

    heartbeat_sec = int(_get_str("HEARTBEAT_SEC", "45"))
    bg_heartbeat_sec = int(_get_str("BG_HEARTBEAT_SEC", "180"))

    inbox_dir = Path(_get_str("INBOX_DIR", str(base_dir / "data" / "inbox"))).expanduser()

    memory_dir = Path(_get_str("MEMORY_DIR", str(base_dir / "memory"))).expanduser()
    memory_enabled = (_get_str("MEMORY_ENABLED", "1") or "1").lower() in ("1", "true", "yes", "y")
    memory_auto_rebuild = (_get_str("MEMORY_AUTO_REBUILD", "1") or "1").lower() in ("1", "true", "yes", "y")
    memory_max_snippets = int(_get_str("MEMORY_MAX_SNIPPETS", "6"))
    memory_snippet_chars = int(_get_str("MEMORY_SNIPPET_CHARS", "500"))

    stt_enabled = (_get_str("STT_ENABLED", "1") or "1").lower() in ("1", "true", "yes", "y")
    stt_model = _get_str("STT_MODEL", "whisper-large-v3") or "whisper-large-v3"
    stt_language = _get_str("STT_LANGUAGE")
    stt_prompt = _get_str("STT_PROMPT")
    stt_max_chars = int(_get_str("STT_MAX_CHARS", "6000"))
    stt_timeout_sec = int(_get_str("STT_TIMEOUT_SEC", "180"))
    stt_keep_files = (_get_str("STT_KEEP_FILES", "0") or "0").lower() in ("1", "true", "yes", "y")

    oauth_auto_peekaboo = (_get_str("OAUTH_AUTO_PEEKABOO", "1") or "1").lower() in ("1", "true", "yes", "y")
    oauth_auto_allow = (_get_str("OAUTH_AUTO_ALLOW", "0") or "0").lower() in ("1", "true", "yes", "y")
    oauth_browser_app = _get_str("OAUTH_BROWSER_APP", "Google Chrome") or "Google Chrome"

    agent_cmd_raw = _get_str("AGENT_CMD")
    agent_cmd = shlex.split(agent_cmd_raw) if agent_cmd_raw else None

    codex_sandbox = _get_str("CODEX_SANDBOX", "workspace-write") or "workspace-write"
    codex_model = _get_str("CODEX_MODEL")
    codex_extra_args = shlex.split(_get_str("CODEX_ARGS", "") or "")

    return Settings(
        base_dir=base_dir,
        token=token,
        allowed_user_id=allowed_user_id,
        allowed_username=allowed_username,
        claim_code=claim_code,
        state_path=state_path,
        max_turns=max_turns,
        agent=agent,
        agent_cmd=agent_cmd,
        agent_workdir=agent_workdir,
        agent_timeout_sec=agent_timeout_sec,
        heartbeat_sec=heartbeat_sec,
        bg_heartbeat_sec=bg_heartbeat_sec,
        inbox_dir=inbox_dir,
        memory_dir=memory_dir,
        memory_enabled=memory_enabled,
        memory_auto_rebuild=memory_auto_rebuild,
        memory_max_snippets=memory_max_snippets,
        memory_snippet_chars=memory_snippet_chars,
        stt_enabled=stt_enabled,
        stt_model=stt_model,
        stt_language=stt_language,
        stt_prompt=stt_prompt,
        stt_max_chars=stt_max_chars,
        stt_timeout_sec=stt_timeout_sec,
        stt_keep_files=stt_keep_files,
        oauth_auto_peekaboo=oauth_auto_peekaboo,
        oauth_auto_allow=oauth_auto_allow,
        oauth_browser_app=oauth_browser_app,
        codex_sandbox=codex_sandbox,
        codex_model=codex_model,
        codex_extra_args=codex_extra_args,
    )
