from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .bg_jobs import BgJobManager


def build_bg_job_actions(bg: BgJobManager, *, chat_id: int, limit: int = 2) -> InlineKeyboardMarkup | None:
    active = bg.active_for_chat(chat_id)
    if not active:
        return None

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Jobs", callback_data="bg:jobs")],
    ]

    for j in active[: max(1, int(limit))]:
        rows.append(
            [
                InlineKeyboardButton(f"Tail #{j.job_id}", callback_data=f"bg:tail:{j.job_id}"),
                InlineKeyboardButton(f"Cancel #{j.job_id}", callback_data=f"bg:cancel:{j.job_id}"),
            ]
        )

    return InlineKeyboardMarkup(rows)


def build_bg_cancel_confirm(*, job_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Confirm cancel", callback_data=f"bg:cancel_confirm:{job_id}"),
            InlineKeyboardButton("Keep running", callback_data="bg:cancel_abort"),
        ]
    ]
    return InlineKeyboardMarkup(rows)

