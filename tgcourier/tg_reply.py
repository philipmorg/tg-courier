from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from .tg_actions import build_bg_job_actions
from .tg_text import send_chat, send_update


async def send_update_with_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    bg = context.bot_data.get("bg_jobs")
    chat = update.effective_chat
    markup = None
    if bg and chat:
        markup = build_bg_job_actions(bg, chat_id=chat.id)
    await send_update(update, text, reply_markup=markup)


async def send_chat_with_actions(bot, *, bg, chat_id: int, text: str) -> None:
    markup = build_bg_job_actions(bg, chat_id=chat_id) if bg else None
    await send_chat(bot, chat_id, text, reply_markup=markup)

