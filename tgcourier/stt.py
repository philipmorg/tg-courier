from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
from pathlib import Path

from .config import Settings


class TranscriptionError(RuntimeError):
    pass


async def _run(argv: list[str], *, timeout_sec: int) -> tuple[int, str]:
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


async def transcribe_file(path: Path, *, settings: Settings, logger) -> str:
    if not settings.stt_enabled:
        raise TranscriptionError("STT disabled (STT_ENABLED=0)")
    if not shutil.which("ffmpeg"):
        raise TranscriptionError("ffmpeg not found")
    if not shutil.which("llm"):
        raise TranscriptionError("llm not found")

    with tempfile.TemporaryDirectory(prefix="tg-courier-stt-") as td:
        td_path = Path(td)
        mp3 = td_path / "audio.mp3"

        code, out = await _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(mp3),
            ],
            timeout_sec=min(settings.stt_timeout_sec, 120),
        )
        if code != 0 or not mp3.exists():
            raise TranscriptionError(f"ffmpeg failed ({code}): {out.strip()[:400]}")

        cmd = [
            "llm",
            "groq-whisper",
            "--model",
            settings.stt_model,
            "--response-format",
            "text",
        ]
        if settings.stt_language:
            cmd.extend(["--language", settings.stt_language])
        if settings.stt_prompt:
            cmd.extend(["--prompt", settings.stt_prompt])
        cmd.append(str(mp3))

        code, out = await _run(cmd, timeout_sec=settings.stt_timeout_sec)
        if code != 0:
            raise TranscriptionError(f"transcription failed ({code}): {out.strip()[:400]}")

        text = out.strip()
        if not text:
            raise TranscriptionError("empty transcription output")

        if settings.stt_max_chars and len(text) > settings.stt_max_chars:
            text = text[: settings.stt_max_chars] + "…"

        logger.info("stt ok chars=%s", len(text))
        return text


def cleanup_file(path: Path) -> None:
    with contextlib.suppress(Exception):
        path.unlink()

