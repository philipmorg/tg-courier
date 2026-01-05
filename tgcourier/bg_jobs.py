from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from datetime import timedelta

from .tg_text import send_chat


@dataclass
class BgJob:
    job_id: int
    chat_id: int
    title: str
    cmd: list[str]
    cwd: Path
    created_ms: int
    log_path: Path

    proc: asyncio.subprocess.Process | None = None
    pid: int | None = None
    started_ms: int | None = None
    ended_ms: int | None = None
    exit_code: int | None = None
    status_message_id: int | None = None


def _tail_text(path: Path, *, max_chars: int = 2600) -> str:
    if not path.exists():
        return ""
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(data) <= max_chars:
        return data
    return "…\n" + data[-max_chars:]


class BgJobManager:
    def __init__(self, *, bot, base_dir: Path, logger, heartbeat_sec: int = 180) -> None:
        self._bot = bot
        self._logger = logger
        self._heartbeat_sec = int(heartbeat_sec)
        self._lock = asyncio.Lock()
        self._next_id = 1
        self._jobs: dict[int, BgJob] = {}
        self._by_chat: dict[int, list[int]] = {}

        self._dir = base_dir / "data" / "bg-jobs"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _fmt_elapsed(self, *, started_ms: int | None) -> str:
        if not started_ms:
            return "0s"
        sec = max(0, int((time.time() * 1000 - started_ms) / 1000))
        return str(timedelta(seconds=sec))

    def _status_text(self, job: BgJob) -> str:
        elapsed = self._fmt_elapsed(started_ms=job.started_ms)
        tail = _tail_text(job.log_path, max_chars=1200).strip()
        lines = [
            f"BG job #{job.job_id} running",
            job.title,
            f"Elapsed: {elapsed}",
            f"Log: {job.log_path}",
            "Commands: /job {id} | /job_tail {id} | /job_cancel {id}".format(id=job.job_id),
        ]
        if tail:
            lines.append("")
            lines.append("Tail:")
            lines.append(tail)
        text = "\n".join(lines).strip()
        return text[:3900] if len(text) > 3900 else text

    async def _ensure_status_message(self, job: BgJob) -> None:
        if self._heartbeat_sec <= 0:
            return
        if job.status_message_id is not None:
            return
        try:
            msg = await self._bot.send_message(
                chat_id=job.chat_id,
                text=self._status_text(job),
                disable_notification=True,
                disable_web_page_preview=True,
            )
            job.status_message_id = getattr(msg, "message_id", None)
        except Exception as e:
            self._logger.info("bg status send failed job_id=%s err=%s", job.job_id, e)

    async def _status_loop(self, job: BgJob) -> None:
        if self._heartbeat_sec <= 0:
            return
        await self._ensure_status_message(job)
        if job.status_message_id is None:
            return

        while True:
            await asyncio.sleep(self._heartbeat_sec)
            if job.ended_ms is not None:
                return
            try:
                await self._bot.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.status_message_id,
                    text=self._status_text(job),
                    disable_web_page_preview=True,
                )
            except Exception as e:
                self._logger.info("bg status edit failed job_id=%s err=%s", job.job_id, e)

    async def start(
        self,
        *,
        chat_id: int,
        title: str,
        cmd: list[str],
        cwd: Path,
    ) -> BgJob:
        async with self._lock:
            job_id = self._next_id
            self._next_id += 1
            log_path = self._dir / f"{job_id}.log"
            job = BgJob(
                job_id=job_id,
                chat_id=chat_id,
                title=title.strip() or "(background job)",
                cmd=list(cmd),
                cwd=cwd,
                created_ms=int(time.time() * 1000),
                log_path=log_path,
            )
            self._jobs[job_id] = job
            self._by_chat.setdefault(chat_id, []).append(job_id)

        asyncio.create_task(self._run(job))
        return job

    def active_for_chat(self, chat_id: int) -> list[BgJob]:
        ids = self._by_chat.get(chat_id, [])
        jobs = [self._jobs[i] for i in ids if i in self._jobs]
        active: list[BgJob] = []
        for j in jobs:
            if j.ended_ms is not None:
                continue
            active.append(j)
        active.sort(key=lambda j: j.job_id)
        return active

    async def list_for_chat(self, chat_id: int, *, limit: int = 20) -> list[BgJob]:
        async with self._lock:
            ids = list(reversed(self._by_chat.get(chat_id, [])))[: max(1, int(limit))]
            return [self._jobs[i] for i in ids if i in self._jobs]

    async def get(self, job_id: int) -> BgJob | None:
        async with self._lock:
            return self._jobs.get(int(job_id))

    async def cancel(self, job_id: int) -> bool:
        job = await self.get(job_id)
        if not job:
            return False
        proc = job.proc
        if not proc or proc.returncode is not None:
            return False

        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return False

        async def hard_kill() -> None:
            await asyncio.sleep(10)
            if proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.kill()

        asyncio.create_task(hard_kill())
        return True

    async def _run(self, job: BgJob) -> None:
        job.started_ms = int(time.time() * 1000)
        env = os.environ.copy()

        job.log_path.parent.mkdir(parents=True, exist_ok=True)
        job.log_path.write_text("", encoding="utf-8")

        status_task: asyncio.Task | None = None
        if self._heartbeat_sec > 0:
            status_task = asyncio.create_task(self._status_loop(job))

        self._logger.info(
            "bg start job_id=%s chat_id=%s cwd=%s cmd=%s",
            job.job_id,
            job.chat_id,
            job.cwd,
            job.cmd,
        )

        with job.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(job.cmd)}\n")
            fh.flush()

            proc = await asyncio.create_subprocess_exec(
                *job.cmd,
                cwd=str(job.cwd),
                env=env,
                stdout=fh,
                stderr=fh,
            )
            job.proc = proc
            job.pid = proc.pid

            rc = await proc.wait()
            job.exit_code = int(rc) if rc is not None else None
            job.ended_ms = int(time.time() * 1000)

        if status_task:
            status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await status_task
        if job.status_message_id is not None:
            with contextlib.suppress(Exception):
                await self._bot.delete_message(chat_id=job.chat_id, message_id=job.status_message_id)

        self._logger.info(
            "bg done job_id=%s chat_id=%s rc=%s",
            job.job_id,
            job.chat_id,
            job.exit_code,
        )

        tail = _tail_text(job.log_path)
        msg = f"Background job #{job.job_id} finished (exit={job.exit_code}).\n{job.title}\nLog: {job.log_path}"
        if tail.strip():
            msg += "\n\nTail:\n```" + "\n" + tail.strip() + "\n```"
        await send_chat(self._bot, job.chat_id, msg)
