from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time


PING_TEXT = "working…"


@dataclass
class Heartbeat:
    message_id: int | None = None


async def heartbeat_loop(*, bot, chat_id: int, interval_sec: int, hb: Heartbeat, logger) -> None:
    interval = max(5, int(interval_sec))
    await asyncio.sleep(interval)
    while True:
        stamp = time.strftime("%H:%M:%S", time.localtime())
        text = f"{PING_TEXT} (ping {stamp})"
        try:
            if hb.message_id is None:
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    disable_notification=True,
                    disable_web_page_preview=True,
                )
                hb.message_id = msg.message_id
            else:
                await bot.edit_message_text(chat_id=chat_id, message_id=hb.message_id, text=text)
        except Exception as e:
            logger.info("heartbeat send/edit failed chat_id=%s err=%s", chat_id, e)
            hb.message_id = None
        await asyncio.sleep(interval)

