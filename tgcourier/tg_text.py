from __future__ import annotations

import html

from telegram.constants import ParseMode
from telegram.error import BadRequest


def chunk(text: str, limit: int = 3900) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["(empty)"]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit * 0.6:
            cut = limit
        chunk_s = remaining[:cut].rstrip()
        chunks.append(chunk_s or remaining[:limit])
        remaining = remaining[cut:].lstrip()
    return chunks


def render_telegram_html(text: str) -> str:
    s = text or ""
    out: list[str] = []
    bold = False
    code = False
    pre = False
    i = 0

    def close_all() -> None:
        nonlocal bold, code, pre
        if code:
            out.append("</code>")
            code = False
        if pre:
            out.append("</code></pre>")
            pre = False
        if bold:
            out.append("</b>")
            bold = False

    while i < len(s):
        if not code and s.startswith("```", i):
            if pre:
                out.append("</code></pre>")
                pre = False
            else:
                if bold:
                    out.append("</b>")
                    bold = False
                out.append("<pre><code>")
                pre = True
            i += 3
            continue

        if not pre and s[i] == "`":
            if code:
                out.append("</code>")
                code = False
            else:
                if bold:
                    out.append("</b>")
                    bold = False
                out.append("<code>")
                code = True
            i += 1
            continue

        if not (code or pre) and s.startswith("**", i):
            if bold:
                out.append("</b>")
                bold = False
            else:
                out.append("<b>")
                bold = True
            i += 2
            continue

        if not (code or pre) and s[i] == "[":
            j = s.find("](", i)
            if j != -1:
                k = s.find(")", j + 2)
                if k != -1:
                    label = s[i + 1 : j]
                    url = s[j + 2 : k]
                    if url.startswith("http://") or url.startswith("https://"):
                        out.append(
                            f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
                        )
                        i = k + 1
                        continue

        out.append(html.escape(s[i]))
        i += 1

    close_all()
    return "".join(out)


async def send_chat(bot, chat_id: int, text: str, *, reply_markup=None) -> None:
    chunks = chunk(text)
    for i, c in enumerate(chunks):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=render_telegram_html(c),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup if i == len(chunks) - 1 else None,
            )
        except BadRequest:
            await bot.send_message(
                chat_id=chat_id,
                text=c,
                disable_web_page_preview=True,
                reply_markup=reply_markup if i == len(chunks) - 1 else None,
            )


async def send_update(update, text: str, *, reply_markup=None) -> None:
    if not update.effective_chat:
        return
    chunks = chunk(text)
    for i, c in enumerate(chunks):
        try:
            await update.effective_chat.send_message(
                render_telegram_html(c),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup if i == len(chunks) - 1 else None,
            )
        except BadRequest:
            await update.effective_chat.send_message(
                c,
                disable_web_page_preview=True,
                reply_markup=reply_markup if i == len(chunks) - 1 else None,
            )
