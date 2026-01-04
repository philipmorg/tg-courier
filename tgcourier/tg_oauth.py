from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from .config import Settings


async def run_cmd(argv: list[str], *, timeout_sec: int = 30) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, ""
    out = (out_b or b"").decode("utf-8", errors="replace")
    return int(proc.returncode or 0), out


async def oauth_peekaboo_flow(*, url: str, settings: Settings, logger) -> None:
    parsed = urlparse(url)
    if parsed.netloc != "accounts.google.com":
        return
    if not settings.oauth_auto_peekaboo:
        return

    browser = settings.oauth_browser_app

    await run_cmd(["/usr/bin/open", "-a", browser, url], timeout_sec=10)
    await run_cmd(["peekaboo", "app", "switch", "--to", browser], timeout_sec=10)

    click_labels = ["Continue", "Next"]
    if settings.oauth_auto_allow:
        click_labels.append("Allow")

    start = time.time()
    while time.time() - start < 180:
        progressed = False
        for label in click_labels:
            code, _out = await run_cmd(
                [
                    "peekaboo",
                    "click",
                    label,
                    "--app",
                    browser,
                    "--wait-for",
                    "5000",
                    "--space-switch",
                ],
                timeout_sec=12,
            )
            if code == 0:
                logger.info("oauth peekaboo clicked=%s", label)
                progressed = True
                await asyncio.sleep(1.0)
        if not progressed:
            await asyncio.sleep(2.0)

