from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
import time

from telegram.error import TelegramError

from .agent import Agent
from .bg_jobs import BgJobManager
from .config import Settings
from .heartbeat import Heartbeat, heartbeat_loop
from .memory import MemoryStore
from .state import StateStore, render_prompt
from .stt import TranscriptionError, cleanup_file, transcribe_file
from .tool_directives import extract_detach_directive
from .tg_oauth import oauth_peekaboo_flow
from .tg_text import send_chat


@dataclass(frozen=True)
class Job:
    job_id: int
    kind: str  # "text" | "audio"
    text: str
    created_ms: int
    file_id: str | None = None
    message_id: int | None = None
    caption: str | None = None


class ChatQueue:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.next_id = 1
        self.queue: list[Job] = []
        self.current: Job | None = None
        self.worker_task: asyncio.Task | None = None


class QueueManager:
    def __init__(
        self,
        *,
        bot,
        agent: Agent,
        settings: Settings,
        store: StateStore,
        state_lock: asyncio.Lock,
        memory: MemoryStore,
        bg: BgJobManager,
        logger,
        system_prompt: str,
    ) -> None:
        self._bot = bot
        self._agent = agent
        self._settings = settings
        self._store = store
        self._state_lock = state_lock
        self._memory = memory
        self._bg = bg
        self._logger = logger
        self._system_prompt = system_prompt
        self._oauth_lock = asyncio.Lock()
        self._chats: dict[int, ChatQueue] = {}

        self._settings.inbox_dir.mkdir(parents=True, exist_ok=True)

    def _get(self, chat_id: int) -> ChatQueue:
        cq = self._chats.get(chat_id)
        if cq is None:
            cq = ChatQueue()
            self._chats[chat_id] = cq
        return cq

    async def enqueue_text(self, chat_id: int, text: str) -> tuple[int, int, bool]:
        cq = self._get(chat_id)
        async with cq.lock:
            job = Job(
                job_id=cq.next_id,
                kind="text",
                text=text,
                created_ms=int(time.time() * 1000),
            )
            cq.next_id += 1
            cq.queue.append(job)
            running = cq.current is not None
            pos = len(cq.queue)
            started = False
            if cq.worker_task is None or cq.worker_task.done():
                cq.worker_task = asyncio.create_task(self._worker(chat_id))
                started = True
        return job.job_id, pos, started and not running

    async def enqueue_audio(
        self,
        *,
        chat_id: int,
        file_id: str,
        message_id: int | None,
        caption: str | None,
    ) -> tuple[int, int, bool]:
        cq = self._get(chat_id)
        async with cq.lock:
            job = Job(
                job_id=cq.next_id,
                kind="audio",
                text="",
                created_ms=int(time.time() * 1000),
                file_id=file_id,
                message_id=message_id,
                caption=caption,
            )
            cq.next_id += 1
            cq.queue.append(job)
            running = cq.current is not None
            pos = len(cq.queue)
            started = False
            if cq.worker_task is None or cq.worker_task.done():
                cq.worker_task = asyncio.create_task(self._worker(chat_id))
                started = True
        return job.job_id, pos, started and not running

    async def snapshot(self, chat_id: int) -> tuple[Job | None, list[Job]]:
        cq = self._get(chat_id)
        async with cq.lock:
            return cq.current, list(cq.queue)

    async def drop_pending(self, chat_id: int) -> int:
        cq = self._get(chat_id)
        async with cq.lock:
            n = len(cq.queue)
            cq.queue.clear()
            return n

    async def cancel_and_clear(self, chat_id: int) -> None:
        cq = self._get(chat_id)
        task: asyncio.Task | None = None
        async with cq.lock:
            cq.queue.clear()
            task = cq.worker_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(Exception):
                await task

    async def _worker(self, chat_id: int) -> None:
        cq = self._get(chat_id)
        while True:
            async with cq.lock:
                if not cq.queue:
                    cq.current = None
                    return
                job = cq.queue.pop(0)
                cq.current = job

            await self._bot.send_message(
                chat_id=chat_id,
                text=f"Working on #{job.job_id}…",
                disable_notification=True,
            )
            try:
                await self._run_job(chat_id, job)
            except asyncio.CancelledError:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text="Canceled.",
                    disable_notification=True,
                )
                raise
            except Exception as e:
                self._logger.exception("job failed chat_id=%s job_id=%s err=%s", chat_id, job.job_id, e)
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=f"Job #{job.job_id} error: {type(e).__name__}: {e}",
                    disable_notification=True,
                )
            finally:
                async with cq.lock:
                    cq.current = None

    async def _download_telegram_file(self, *, chat_id: int, file_id: str, message_id: int | None, job_id: int) -> Path:
        f = await self._bot.get_file(file_id)
        file_path = getattr(f, "file_path", "") or ""
        suffix = Path(file_path).suffix if file_path else ""
        if not suffix:
            suffix = ".bin"

        safe_id = message_id if message_id is not None else job_id
        dest_dir = self._settings.inbox_dir / str(chat_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{safe_id}{suffix}"

        await f.download_to_drive(custom_path=str(dest))
        return dest

    async def _run_job(self, chat_id: int, job: Job) -> None:
        settings = self._settings
        store = self._store

        user_text = job.text
        audio_path: Path | None = None

        if job.kind == "audio":
            if not job.file_id:
                await send_chat(self._bot, chat_id, "Missing audio file_id; skipping.")
                return

            await self._bot.send_message(
                chat_id=chat_id,
                text=f"#{job.job_id}: downloading audio…",
                disable_notification=True,
            )

            audio_path = await self._download_telegram_file(
                chat_id=chat_id,
                file_id=job.file_id,
                message_id=job.message_id,
                job_id=job.job_id,
            )

            await self._bot.send_message(
                chat_id=chat_id,
                text=f"#{job.job_id}: transcribing…",
                disable_notification=True,
            )

            try:
                transcription = await transcribe_file(audio_path, settings=settings, logger=self._logger)
            except TranscriptionError as e:
                await send_chat(self._bot, chat_id, f"Transcription failed: {e}")
                return
            finally:
                if audio_path and not settings.stt_keep_files:
                    cleanup_file(audio_path)

            if job.caption:
                user_text = f"{job.caption}\n\n{transcription}"
            else:
                user_text = transcription

            preview = transcription if len(transcription) <= 1200 else transcription[:1200] + "…"
            await send_chat(self._bot, chat_id, f"Transcription (preview):\n{preview}")

        mem_ctx = self._memory.build_context(user_text) if settings.memory_enabled else ""
        sys_prompt = self._system_prompt
        if mem_ctx:
            sys_prompt = sys_prompt.rstrip() + "\n\n" + mem_ctx

        async with self._state_lock:
            prior = store.get_messages(chat_id, settings.max_turns)
            store.append(chat_id, "user", user_text)
            codex_yolo = bool(store.get_pref(chat_id, "codex_yolo", False))
            codex_sandbox_pref = store.get_pref(chat_id, "codex_sandbox", None)
            codex_sandbox = str(codex_sandbox_pref).strip() if isinstance(codex_sandbox_pref, str) else None

        prompt = render_prompt(sys_prompt, prior, user_text)

        hb = Heartbeat()
        hb_task = asyncio.create_task(
            heartbeat_loop(
                bot=self._bot,
                chat_id=chat_id,
                interval_sec=settings.heartbeat_sec,
                hb=hb,
                logger=self._logger,
            )
        )

        async def on_oauth_url(url: str) -> None:
            async with self._oauth_lock:
                self._logger.info("oauth detected chat_id=%s url=%s", chat_id, url)
                async with self._state_lock:
                    store.set_pref(chat_id, "pending_oauth_url", url)

                await self._bot.send_message(
                    chat_id=chat_id,
                    text="OAuth detected. Driving Chrome via Peekaboo (auto-click Next/Continue).",
                    disable_notification=True,
                    disable_web_page_preview=True,
                )
                await oauth_peekaboo_flow(url=url, settings=settings, logger=self._logger)
                if not settings.oauth_auto_allow:
                    await self._bot.send_message(
                        chat_id=chat_id,
                        text="If consent is still up, review scopes and click Allow to finish (auto-allow is off).",
                        disable_notification=True,
                        disable_web_page_preview=True,
                    )

        try:
            reply = await self._agent.ask(
                prompt,
                yolo=codex_yolo,
                sandbox=codex_sandbox,
                on_oauth_url=on_oauth_url,
            )
        except TimeoutError:
            await send_chat(
                self._bot,
                chat_id,
                "Agent timeout. Set `AGENT_TIMEOUT_SEC=0` (recommended) or increase it to allow longer runs.",
            )
            return
        except asyncio.CancelledError:
            raise
        except TelegramError as e:
            await send_chat(self._bot, chat_id, f"Telegram error: {e}")
            return
        finally:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task
            if hb.message_id is not None:
                with contextlib.suppress(Exception):
                    await self._bot.delete_message(chat_id=chat_id, message_id=hb.message_id)

        detach_spec, cleaned = extract_detach_directive(reply.text)
        if detach_spec:
            cmd_obj = detach_spec.get("cmd")
            if isinstance(cmd_obj, str) and cmd_obj.strip():
                cmd = ["/bin/zsh", "-lc", cmd_obj.strip()]
            elif isinstance(cmd_obj, list) and all(isinstance(x, str) and x.strip() for x in cmd_obj):
                cmd = [str(x) for x in cmd_obj]
            else:
                msg = "Bad DETACH directive: missing cmd (string or string list)."
                async with self._state_lock:
                    store.append(chat_id, "assistant", msg)
                await send_chat(self._bot, chat_id, msg)
                return

            title = str(detach_spec.get("title") or "").strip() or "Detached job"

            cwd_raw = detach_spec.get("cwd")
            cwd = settings.agent_workdir
            if isinstance(cwd_raw, str) and cwd_raw.strip():
                p = Path(cwd_raw).expanduser()
                cwd = (settings.agent_workdir / p) if not p.is_absolute() else p

            bg_job = await self._bg.start(chat_id=chat_id, title=title, cmd=cmd, cwd=cwd)
            msg = f"Launched background job #{bg_job.job_id}. {title}\nLog: {bg_job.log_path}\nUse /jobs, /job {bg_job.job_id}, /job_tail {bg_job.job_id}, /job_cancel {bg_job.job_id}."
            if cleaned:
                msg = cleaned.strip() + "\n\n" + msg

            async with self._state_lock:
                store.append(chat_id, "assistant", msg)
            self._logger.info("tx(detach) chat_id=%s job_id=%s bg_job_id=%s", chat_id, job.job_id, bg_job.job_id)
            await send_chat(self._bot, chat_id, msg)
            return

        async with self._state_lock:
            store.append(chat_id, "assistant", reply.text)
        self._logger.info("tx chat_id=%s job_id=%s chars=%s", chat_id, job.job_id, len(reply.text))
        await send_chat(self._bot, chat_id, reply.text)
