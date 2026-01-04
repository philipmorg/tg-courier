from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .config import Settings
from .memory import MemoryStore
from .queue import QueueManager
from .state import StateStore
from .tg_text import send_update


def _resolve_allowed_user_id(settings: Settings, store: StateStore) -> int | None:
    if settings.allowed_user_id is not None:
        return settings.allowed_user_id
    return store.get_claimed_user_id()


def _resolve_allowed_username(settings: Settings) -> str | None:
    return settings.allowed_username.lower() if settings.allowed_username else None


def _is_allowed_user(settings: Settings, store: StateStore, update: Update) -> bool:
    if not update.effective_user:
        return False

    allowed_user_id = _resolve_allowed_user_id(settings, store)
    if allowed_user_id is not None:
        return update.effective_user.id == allowed_user_id

    allowed_username = _resolve_allowed_username(settings)
    if allowed_username:
        username = (update.effective_user.username or "").strip().lower()
        return bool(username) and username == allowed_username

    return False


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]

    lines = [
        "tg-courier commands:",
        "/help",
        "/whoami",
        "/claim <code> (if enabled)",
        "/reset",
        "/status",
        "/queue",
        "/cancel (cancel current + clear queue)",
        "/drop (clear pending queue)",
        "/w (codex) yolo mode on",
        "/ro (codex) yolo mode off",
        "/sandbox_rw (codex) sandbox=workspace-write",
        "/sandbox_ro (codex) sandbox=read-only",
        "/mem <text> append daily",
        "/mem_rebuild rebuild backlinks",
    ]
    if settings.claim_code and _resolve_allowed_user_id(settings, store) is None:
        lines.append("(claim enabled: you must /claim first)")
    await send_update(update, "\n".join(lines))


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    await send_update(
        update,
        "\n".join(
            [
                f"user_id: {user.id if user else 'unknown'}",
                f"username: @{user.username if user and user.username else 'none'}",
                f"chat_id: {chat.id if chat else 'unknown'}",
                f"chat_type: {chat.type if chat else 'unknown'}",
            ]
        ),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    state_lock: asyncio.Lock = context.bot_data["state_lock"]

    async with state_lock:
        codex_yolo = bool(
            update.effective_chat and store.get_pref(update.effective_chat.id, "codex_yolo", False)
        )
        codex_sandbox_pref = (
            store.get_pref(update.effective_chat.id, "codex_sandbox", None)
            if update.effective_chat
            else None
        )
        effective_sandbox = (
            str(codex_sandbox_pref).strip()
            if isinstance(codex_sandbox_pref, str) and str(codex_sandbox_pref).strip()
            else settings.codex_sandbox
        )

    lines = [
        f"agent: {settings.agent}",
        f"workdir: {settings.agent_workdir}",
        f"state: {store.path}",
        f"allowed_user_id: {_resolve_allowed_user_id(settings, store) or '(none)'}",
        f"allowed_username: @{_resolve_allowed_username(settings) or '(none)'}",
        f"codex_sandbox: {effective_sandbox}",
        f"codex_yolo: {codex_yolo}",
        f"heartbeat_sec: {settings.heartbeat_sec}",
        f"inbox_dir: {settings.inbox_dir}",
        f"memory_dir: {settings.memory_dir}",
        f"memory_enabled: {settings.memory_enabled}",
        f"stt_enabled: {settings.stt_enabled}",
        f"stt_model: {settings.stt_model}",
        f"oauth_auto_peekaboo: {settings.oauth_auto_peekaboo}",
        f"oauth_auto_allow: {settings.oauth_auto_allow}",
        f"oauth_browser_app: {settings.oauth_browser_app}",
    ]
    await send_update(update, "\n".join(lines))


async def cmd_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    state_lock: asyncio.Lock = context.bot_data["state_lock"]
    args = context.args or []

    if settings.allowed_user_id is not None or settings.allowed_username is not None:
        await send_update(update, "Already locked via TELEGRAM_ALLOWED_USER_ID/USERNAME.")
        return
    if not settings.claim_code:
        await send_update(update, "Claim disabled (missing TELEGRAM_CLAIM_CODE).")
        return

    current = store.get_claimed_user_id()
    if current is not None:
        await send_update(update, f"Already claimed by user_id {current}.")
        return

    if not args:
        await send_update(update, "Usage: /claim <code>")
        return

    if args[0] != settings.claim_code:
        await send_update(update, "Bad claim code.")
        return

    if not update.effective_user:
        await send_update(update, "No user found on update.")
        return

    async with state_lock:
        store.set_claimed_user_id(update.effective_user.id)
    await send_update(update, f"Claimed. allowed_user_id={update.effective_user.id}")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    qm: QueueManager = context.bot_data["queue_manager"]
    state_lock: asyncio.Lock = context.bot_data["state_lock"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat:
        return

    await qm.cancel_and_clear(update.effective_chat.id)
    async with state_lock:
        store.reset_chat(update.effective_chat.id)
    await send_update(update, "Reset chat history (and canceled queue).")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    qm: QueueManager = context.bot_data["queue_manager"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat:
        return

    current, pending = await qm.snapshot(update.effective_chat.id)
    if not current and not pending:
        await send_update(update, "Queue empty.")
        return

    lines: list[str] = []
    if current:
        lines.append(f"Current: #{current.job_id} ({current.kind})")
    if pending:
        lines.append(f"Pending: {len(pending)}")
        preview = pending[:5]
        for j in preview:
            label = j.text.strip() if j.text.strip() else j.kind
            lines.append(f"- #{j.job_id}: {label[:60]}{'…' if len(label) > 60 else ''}")
        if len(pending) > len(preview):
            lines.append(f"(+{len(pending) - len(preview)} more)")
    await send_update(update, "\n".join(lines))


async def cmd_drop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    qm: QueueManager = context.bot_data["queue_manager"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat:
        return

    n = await qm.drop_pending(update.effective_chat.id)
    await send_update(update, f"Dropped {n} pending job(s).")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    qm: QueueManager = context.bot_data["queue_manager"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat:
        return

    await qm.cancel_and_clear(update.effective_chat.id)
    await send_update(update, "Canceled current job and cleared queue.")


async def cmd_w(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    state_lock: asyncio.Lock = context.bot_data["state_lock"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if settings.agent != "codex":
        await send_update(update, "Not using Codex (AGENT!=codex).")
        return

    async with state_lock:
        store.set_pref(update.effective_chat.id, "codex_yolo", True)
    await send_update(update, "Codex yolo: ON")


async def cmd_ro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    state_lock: asyncio.Lock = context.bot_data["state_lock"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if settings.agent != "codex":
        await send_update(update, "Not using Codex (AGENT!=codex).")
        return

    async with state_lock:
        store.set_pref(update.effective_chat.id, "codex_yolo", False)
    await send_update(update, "Codex yolo: OFF")


async def cmd_sandbox_rw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    state_lock: asyncio.Lock = context.bot_data["state_lock"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if settings.agent != "codex":
        await send_update(update, "Not using Codex (AGENT!=codex).")
        return

    async with state_lock:
        store.set_pref(update.effective_chat.id, "codex_sandbox", "workspace-write")
    await send_update(update, "Codex sandbox: workspace-write")


async def cmd_sandbox_ro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    state_lock: asyncio.Lock = context.bot_data["state_lock"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if settings.agent != "codex":
        await send_update(update, "Not using Codex (AGENT!=codex).")
        return

    async with state_lock:
        store.set_pref(update.effective_chat.id, "codex_sandbox", "read-only")
    await send_update(update, "Codex sandbox: read-only")


async def cmd_mem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    mem: MemoryStore = context.bot_data["memory"]
    logger: logging.Logger = context.bot_data["logger"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if not settings.memory_enabled:
        await send_update(update, "Memory disabled (MEMORY_ENABLED=0).")
        return

    text = " ".join(context.args or []).strip()
    if not text:
        await send_update(update, "Usage: /mem <text> (appends to today’s note)")
        return

    path = mem.append_daily(text)
    logger.info("mem append path=%s", path)
    await send_update(update, f"Saved to: {path}")


async def cmd_mem_rebuild(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    mem: MemoryStore = context.bot_data["memory"]
    logger: logging.Logger = context.bot_data["logger"]

    if not _is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if not settings.memory_enabled:
        await send_update(update, "Memory disabled (MEMORY_ENABLED=0).")
        return

    n = mem.rebuild_backlinks()
    logger.info("mem rebuild updated=%s", n)
    await send_update(update, f"Backlinks rebuilt. Updated {n} files.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    qm: QueueManager = context.bot_data["queue_manager"]
    logger: logging.Logger = context.bot_data["logger"]

    if not update.effective_user or not update.effective_chat or not update.message:
        return
    if update.effective_chat.type != "private":
        return
    if not _is_allowed_user(settings, store, update):
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    logger.info("rx text chat_id=%s", update.effective_chat.id)
    job_id, pos, started = await qm.enqueue_text(update.effective_chat.id, text)
    if started and pos == 1:
        await send_update(update, f"Queued as #{job_id}. Starting now.")
    else:
        await send_update(update, f"Queued as #{job_id} (position {pos}).")


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    qm: QueueManager = context.bot_data["queue_manager"]
    logger: logging.Logger = context.bot_data["logger"]

    if not update.effective_user or not update.effective_chat or not update.message:
        return
    if update.effective_chat.type != "private":
        return
    if not _is_allowed_user(settings, store, update):
        return
    if not update.message.voice:
        return

    file_id = update.message.voice.file_id
    logger.info("rx voice chat_id=%s msg_id=%s", update.effective_chat.id, update.message.message_id)
    job_id, pos, started = await qm.enqueue_audio(
        chat_id=update.effective_chat.id,
        file_id=file_id,
        message_id=update.message.message_id,
        caption=update.message.caption,
    )
    await send_update(update, f"Queued voice as #{job_id} (position {pos}).")


async def on_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    qm: QueueManager = context.bot_data["queue_manager"]
    logger: logging.Logger = context.bot_data["logger"]

    if not update.effective_user or not update.effective_chat or not update.message:
        return
    if update.effective_chat.type != "private":
        return
    if not _is_allowed_user(settings, store, update):
        return

    file_id: str | None = None
    caption = update.message.caption

    if update.message.audio:
        file_id = update.message.audio.file_id
    elif update.message.document:
        mime = (update.message.document.mime_type or "").lower()
        name = (update.message.document.file_name or "").lower()
        if mime.startswith("audio/") or name.endswith((".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac")):
            file_id = update.message.document.file_id

    if not file_id:
        return

    logger.info("rx audio chat_id=%s msg_id=%s", update.effective_chat.id, update.message.message_id)
    job_id, pos, started = await qm.enqueue_audio(
        chat_id=update.effective_chat.id,
        file_id=file_id,
        message_id=update.message.message_id,
        caption=caption,
    )
    await send_update(update, f"Queued audio as #{job_id} (position {pos}).")
