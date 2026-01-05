from __future__ import annotations

import logging

from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes


async def on_telegram_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    python-telegram-bot calls this for exceptions in handlers/polling.
    Without an error handler it logs scary stack traces to stderr.
    """

    logger: logging.Logger = context.application.bot_data.get("logger") or logging.getLogger("tgcourier")
    err = getattr(context, "error", None)

    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("telegram transient network error: %s", err)
        return

    logger.exception("telegram error handler caught exception: %r", err)

