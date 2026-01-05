from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .agent import build_agent
from .bg_jobs import BgJobManager
from .config import load_settings
from .handlers import (
    cmd_cancel,
    cmd_claim,
    cmd_drop,
    cmd_help,
    cmd_mem,
    cmd_mem_rebuild,
    cmd_queue,
    cmd_reset,
    cmd_ro,
    cmd_sandbox_ro,
    cmd_sandbox_rw,
    cmd_status,
    cmd_w,
    cmd_whoami,
    on_audio,
    on_text,
    on_voice,
)
from .handlers_bg import (
    cmd_bg,
    cmd_job,
    cmd_job_cancel,
    cmd_job_tail,
    cmd_jobs,
    on_bg_callback,
)
from .memory import MemoryConfig, MemoryStore
from .prompts import SYSTEM_PROMPT
from .queue import QueueManager
from .state import StateStore


DEFAULT_LOG_NAME = "tg-courier.log"


def setup_logging(*, base_dir: Path, log_path: Path | None, echo_local: bool) -> logging.Logger:
    logger = logging.getLogger("tgcourier")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for h in list(logger.handlers):
        logger.removeHandler(h)

    resolved_log_path = log_path or (base_dir / "data" / DEFAULT_LOG_NAME)
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(resolved_log_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if echo_local:
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger.info("boot echo_local=%s log=%s", echo_local, resolved_log_path)
    return logger


def main(*, echo_local: bool = False, log_path: Path | None = None) -> None:
    base_dir = Path(__file__).resolve().parents[1]
    settings = load_settings(base_dir)
    store = StateStore(settings.state_path)
    agent = build_agent(settings)
    logger = setup_logging(base_dir=base_dir, log_path=log_path, echo_local=echo_local)
    memory = MemoryStore(
        MemoryConfig(
            dir=settings.memory_dir,
            enabled=settings.memory_enabled,
            max_snippets=settings.memory_max_snippets,
            snippet_chars=settings.memory_snippet_chars,
            auto_rebuild=settings.memory_auto_rebuild,
        )
    )

    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    app = Application.builder().token(settings.token).concurrent_updates(True).build()
    state_lock = asyncio.Lock()
    bg = BgJobManager(
        bot=app.bot,
        base_dir=base_dir,
        logger=logger,
        heartbeat_sec=settings.bg_heartbeat_sec,
    )
    queue_manager = QueueManager(
        bot=app.bot,
        agent=agent,
        settings=settings,
        store=store,
        state_lock=state_lock,
        memory=memory,
        bg=bg,
        logger=logger,
        system_prompt=SYSTEM_PROMPT,
    )

    app.bot_data["settings"] = settings
    app.bot_data["store"] = store
    app.bot_data["agent"] = agent
    app.bot_data["logger"] = logger
    app.bot_data["memory"] = memory
    app.bot_data["state_lock"] = state_lock
    app.bot_data["queue_manager"] = queue_manager
    app.bot_data["bg_jobs"] = bg

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("claim", cmd_claim))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("bg", cmd_bg))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("job", cmd_job))
    app.add_handler(CommandHandler("job_tail", cmd_job_tail))
    app.add_handler(CommandHandler("job_cancel", cmd_job_cancel))
    app.add_handler(CallbackQueryHandler(on_bg_callback, pattern=r"^bg:"))
    app.add_handler(CommandHandler("w", cmd_w))
    app.add_handler(CommandHandler("ro", cmd_ro))
    app.add_handler(CommandHandler("sandbox_rw", cmd_sandbox_rw))
    app.add_handler(CommandHandler("sandbox_ro", cmd_sandbox_ro))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("drop", cmd_drop))
    app.add_handler(CommandHandler("mem", cmd_mem))
    app.add_handler(CommandHandler("mem_rebuild", cmd_mem_rebuild))

    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL, on_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling()
