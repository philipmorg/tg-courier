from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from dataclasses import dataclass
from typing import Sequence

from .config import Settings


@dataclass(frozen=True)
class AgentReply:
    text: str
    raw_stdout: str
    raw_stderr: str


class Agent:
    async def ask(
        self,
        prompt: str,
        *,
        yolo: bool = False,
        on_oauth_url: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentReply:
        raise NotImplementedError


class ShellAgent(Agent):
    def __init__(self, cmd: Sequence[str], *, timeout_sec: int, cwd: Path) -> None:
        self._cmd = list(cmd)
        self._timeout_sec = timeout_sec
        self._cwd = cwd

    async def ask(
        self,
        prompt: str,
        *,
        yolo: bool = False,
        on_oauth_url: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentReply:
        proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._cwd),
            env=os.environ.copy(),
        )

        try:
            if self._timeout_sec and self._timeout_sec > 0:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(prompt.encode("utf-8")), timeout=self._timeout_sec
                )
            else:
                stdout_b, stderr_b = await proc.communicate(prompt.encode("utf-8"))
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        except asyncio.CancelledError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        text = stdout.strip() or stderr.strip() or "(no output)"
        return AgentReply(text=text, raw_stdout=stdout, raw_stderr=stderr)


class CodexExecAgent(Agent):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def ask(
        self,
        prompt: str,
        *,
        yolo: bool = False,
        on_oauth_url: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentReply:
        if not shutil.which("codex"):
            raise FileNotFoundError("codex not found on PATH")

        with tempfile.TemporaryDirectory(prefix="tg-courier-codex-") as td:
            out_path = Path(td) / "last_message.txt"
            cmd: list[str] = [
                "codex",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--color",
                "never",
                *(
                    ["--dangerously-bypass-approvals-and-sandbox"]
                    if yolo
                    else ["--sandbox", self._settings.codex_sandbox]
                ),
                "--output-last-message",
                str(out_path),
                "-C",
                str(self._settings.agent_workdir),
            ]

            if self._settings.codex_model:
                cmd.extend(["-m", self._settings.codex_model])
            cmd.extend(self._settings.codex_extra_args)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._settings.agent_workdir),
                env=os.environ.copy(),
            )

            oauth_re = re.compile(r"https://accounts\\.google\\.com/o/oauth2/[^\s\"']+")
            seen_oauth: set[str] = set()

            async def handle_jsonl(line: str) -> None:
                if not on_oauth_url:
                    return
                try:
                    evt = json.loads(line)
                except Exception:
                    return

                item = evt.get("item") if isinstance(evt, dict) else None
                if not isinstance(item, dict):
                    return

                fields: list[str] = []
                for key in ("text", "aggregated_output"):
                    v = item.get(key)
                    if isinstance(v, str) and v:
                        fields.append(v)

                for blob in fields:
                    for m in oauth_re.finditer(blob):
                        url = m.group(0)
                        if url in seen_oauth:
                            continue
                        seen_oauth.add(url)
                        await on_oauth_url(url)

            stdout_parts: list[str] = []
            stderr_parts: list[str] = []

            async def pump_stdout() -> None:
                assert proc.stdout is not None
                while True:
                    line_b = await proc.stdout.readline()
                    if not line_b:
                        break
                    line = line_b.decode("utf-8", errors="replace").rstrip("\n")
                    stdout_parts.append(line + "\n")
                    await handle_jsonl(line)

            async def pump_stderr() -> None:
                assert proc.stderr is not None
                while True:
                    line_b = await proc.stderr.readline()
                    if not line_b:
                        break
                    stderr_parts.append(line_b.decode("utf-8", errors="replace"))

            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            pump_out = asyncio.create_task(pump_stdout())
            pump_err = asyncio.create_task(pump_stderr())

            try:
                if self._settings.agent_timeout_sec and self._settings.agent_timeout_sec > 0:
                    await asyncio.wait_for(proc.wait(), timeout=self._settings.agent_timeout_sec)
                else:
                    await proc.wait()
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            except asyncio.CancelledError:
                proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
                raise
            finally:
                await pump_out
                await pump_err

            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)
            text = ""
            if out_path.exists():
                text = out_path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                text = stdout.strip()
            if not text:
                text = stderr.strip()
            if not text:
                text = "(no output)"

            return AgentReply(text=text, raw_stdout=stdout, raw_stderr=stderr)


def build_agent(settings: Settings) -> Agent:
    agent = settings.agent

    if agent == "shell":
        if not settings.agent_cmd:
            raise SystemExit("AGENT=shell requires AGENT_CMD")
        return ShellAgent(
            settings.agent_cmd,
            timeout_sec=settings.agent_timeout_sec,
            cwd=settings.agent_workdir,
        )

    if agent in ("codex", "auto"):
        return CodexExecAgent(settings)

    raise SystemExit(f"Unknown AGENT={agent!r}; use codex|shell")
