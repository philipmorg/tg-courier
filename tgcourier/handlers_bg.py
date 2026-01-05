from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from .auth import is_allowed_user
from .bg_jobs import BgJobManager
from .config import Settings
from .state import StateStore
from .tg_actions import build_bg_cancel_confirm, build_bg_job_actions
from .tg_reply import send_update_with_actions


async def cmd_bg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    bg: BgJobManager = context.bot_data["bg_jobs"]
    logger: logging.Logger = context.bot_data["logger"]

    if not is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return

    cmd_text = " ".join(context.args or []).strip()
    if not cmd_text:
        await send_update_with_actions(update, context, "Usage: /bg <command> (runs detached; bot remains usable)")
        return

    cmd = ["/bin/zsh", "-lc", cmd_text]
    title = cmd_text if len(cmd_text) <= 80 else cmd_text[:80] + "…"
    job = await bg.start(chat_id=update.effective_chat.id, title=title, cmd=cmd, cwd=settings.agent_workdir)
    logger.info("bg launched job_id=%s chat_id=%s", job.job_id, update.effective_chat.id)
    await send_update_with_actions(update, context, f"Launched background job #{job.job_id}. Log: {job.log_path}")


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    bg: BgJobManager = context.bot_data["bg_jobs"]

    if not is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return

    jobs = await bg.list_for_chat(update.effective_chat.id, limit=20)
    if not jobs:
        await send_update_with_actions(update, context, "No background jobs yet.")
        return

    lines: list[str] = ["Background jobs:"]
    for j in jobs[:20]:
        if j.ended_ms is None:
            status = "running"
        else:
            status = f"done exit={j.exit_code}"
        lines.append(f"- #{j.job_id}: {status} — {j.title}")
    await send_update_with_actions(update, context, "\n".join(lines))


async def cmd_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    bg: BgJobManager = context.bot_data["bg_jobs"]

    if not is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return

    if not context.args:
        await send_update_with_actions(update, context, "Usage: /job <id>")
        return
    try:
        job_id = int(context.args[0])
    except ValueError:
        await send_update_with_actions(update, context, "Usage: /job <id> (id must be an integer)")
        return

    j = await bg.get(job_id)
    if not j or j.chat_id != update.effective_chat.id:
        await send_update_with_actions(update, context, f"Job #{job_id} not found.")
        return

    status = "running" if j.ended_ms is None else f"done exit={j.exit_code}"
    await send_update_with_actions(
        update,
        context,
        "\n".join(
            [
                f"job: #{j.job_id}",
                f"status: {status}",
                f"pid: {j.pid or '(none)'}",
                f"cwd: {j.cwd}",
                f"log: {j.log_path}",
                f"title: {j.title}",
                f"cmd: {' '.join(j.cmd)}",
            ]
        ),
    )


async def cmd_job_tail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    bg: BgJobManager = context.bot_data["bg_jobs"]

    if not is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return

    if not context.args:
        await send_update_with_actions(update, context, "Usage: /job_tail <id>")
        return
    try:
        job_id = int(context.args[0])
    except ValueError:
        await send_update_with_actions(update, context, "Usage: /job_tail <id> (id must be an integer)")
        return

    j = await bg.get(job_id)
    if not j or j.chat_id != update.effective_chat.id:
        await send_update_with_actions(update, context, f"Job #{job_id} not found.")
        return

    if not j.log_path.exists():
        await send_update_with_actions(update, context, f"No log yet for job #{job_id}.")
        return

    tail = j.log_path.read_text(encoding="utf-8", errors="replace")
    tail = tail[-2800:] if len(tail) > 2800 else tail
    await send_update_with_actions(update, context, f"Tail for #{j.job_id}:\n```{tail.strip()}```")


async def cmd_job_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    bg: BgJobManager = context.bot_data["bg_jobs"]

    if not is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return

    if not context.args:
        await send_update_with_actions(update, context, "Usage: /job_cancel <id>")
        return
    try:
        job_id = int(context.args[0])
    except ValueError:
        await send_update_with_actions(update, context, "Usage: /job_cancel <id> (id must be an integer)")
        return

    j = await bg.get(job_id)
    if not j or j.chat_id != update.effective_chat.id:
        await send_update_with_actions(update, context, f"Job #{job_id} not found.")
        return

    ok = await bg.cancel(job_id)
    await send_update_with_actions(update, context, "Cancel requested." if ok else "Not running (or already finished).")


async def on_bg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    store: StateStore = context.bot_data["store"]
    bg: BgJobManager = context.bot_data["bg_jobs"]

    q = update.callback_query
    if not q:
        return
    await q.answer()

    if not is_allowed_user(settings, store, update):
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return

    data = (q.data or "").strip()
    if not data.startswith("bg:"):
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "jobs":
        await cmd_jobs(update, context)
        return

    if action == "tail" and len(parts) == 3:
        try:
            job_id = int(parts[2])
        except ValueError:
            return
        j = await bg.get(job_id)
        if not j or j.chat_id != update.effective_chat.id:
            await send_update_with_actions(update, context, f"Job #{job_id} not found.")
            return
        if not j.log_path.exists():
            await send_update_with_actions(update, context, f"No log yet for job #{job_id}.")
            return
        tail = j.log_path.read_text(encoding="utf-8", errors="replace")
        tail = tail[-2800:] if len(tail) > 2800 else tail
        await send_update_with_actions(update, context, f"Tail for #{j.job_id}:\n```{tail.strip()}```")
        return

    if action == "cancel" and len(parts) == 3:
        try:
            job_id = int(parts[2])
        except ValueError:
            return
        if q.message:
            await q.message.reply_text(
                f"Cancel background job #{job_id}? (double-confirm)",
                reply_markup=build_bg_cancel_confirm(job_id=job_id),
                disable_notification=True,
            )
        return

    if action == "cancel_confirm" and len(parts) == 3:
        try:
            job_id = int(parts[2])
        except ValueError:
            return
        ok = await bg.cancel(job_id)
        if q.message:
            await q.message.edit_text(
                "Cancel requested." if ok else "Not running (or already finished).",
                reply_markup=build_bg_job_actions(bg, chat_id=update.effective_chat.id),
                disable_web_page_preview=True,
            )
        return

    if action == "cancel_abort":
        if q.message:
            await q.message.edit_text(
                "OK — leaving it running.",
                reply_markup=build_bg_job_actions(bg, chat_id=update.effective_chat.id),
                disable_web_page_preview=True,
            )
        return
