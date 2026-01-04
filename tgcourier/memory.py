from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BACKLINKS_START = "<!-- tg-courier:backlinks:start -->"
BACKLINKS_END = "<!-- tg-courier:backlinks:end -->"

WIKILINK_RE = re.compile(r"\[\[([^\[\]#|]+?)(?:\|[^\]]+)?\]\]")


def _safe_path_from_title(title: str) -> Path:
    raw = title.strip()
    if not raw:
        raise ValueError("empty title")

    parts = []
    for seg in raw.split("/"):
        seg = seg.strip().replace(":", "-")
        seg = re.sub(r"[<>\"\\\\|?*\u0000-\u001f]", "", seg)
        seg = seg.strip().strip(".")
        if not seg:
            continue
        parts.append(seg)

    if not parts:
        raise ValueError(f"bad title: {title!r}")

    return Path(*parts).with_suffix(".md")


def _title_from_path(path: Path) -> str:
    return path.stem


def _relative_link(from_file: Path, to_file: Path) -> str:
    try:
        rel = to_file.relative_to(from_file.parent)
    except ValueError:
        rel = Path(*to_file.parts)
    return str(rel)


@dataclass(frozen=True)
class MemoryConfig:
    dir: Path
    enabled: bool = True
    max_snippets: int = 6
    snippet_chars: int = 500
    auto_rebuild: bool = True


class MemoryStore:
    def __init__(self, cfg: MemoryConfig) -> None:
        self._cfg = cfg
        self._cfg.dir.mkdir(parents=True, exist_ok=True)

    @property
    def dir(self) -> Path:
        return self._cfg.dir

    def _iter_notes(self) -> list[Path]:
        return sorted(
            p
            for p in self._cfg.dir.rglob("*.md")
            if p.is_file() and not p.name.startswith(".")
        )

    def append_daily(self, text: str, *, now: datetime | None = None) -> Path:
        now = now or datetime.now()
        day_name = now.strftime("%Y-%m-%d")
        path = self._cfg.dir / f"{day_name}.md"
        stamp = now.strftime("%H:%M")
        line = f"- {stamp} {text.strip()}\n"

        if not path.exists():
            path.write_text(f"# {day_name}\n\n{line}", encoding="utf-8")
        else:
            existing = path.read_text(encoding="utf-8", errors="replace")
            if not existing.endswith("\n"):
                existing += "\n"
            path.write_text(existing + line, encoding="utf-8")

        if self._cfg.auto_rebuild:
            self.rebuild_backlinks()

        return path

    def rebuild_backlinks(self) -> int:
        notes = self._iter_notes()
        backlinks: dict[Path, set[Path]] = {}

        for src in notes:
            body = src.read_text(encoding="utf-8", errors="replace")
            for m in WIKILINK_RE.finditer(body):
                title = m.group(1)
                try:
                    target_rel = _safe_path_from_title(title)
                except ValueError:
                    continue
                target = (self._cfg.dir / target_rel).resolve()
                backlinks.setdefault(target, set()).add(src.resolve())

        updated = 0
        for target, sources in backlinks.items():
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                title = _title_from_path(target)
                target.write_text(f"# {title}\n\n", encoding="utf-8")

            sources_sorted = sorted(sources, key=lambda p: p.as_posix().lower())
            block_lines = [
                BACKLINKS_START,
                f"## Linked references ({len(sources_sorted)})",
            ]
            for src in sources_sorted:
                rel = _relative_link(target, src)
                label = _title_from_path(src)
                block_lines.append(f"- [{label}]({rel})")
            block_lines.append(BACKLINKS_END)
            block = "\n".join(block_lines).strip() + "\n"

            content = target.read_text(encoding="utf-8", errors="replace")
            if BACKLINKS_START in content and BACKLINKS_END in content:
                pre, _mid = content.split(BACKLINKS_START, 1)
                _old, post = _mid.split(BACKLINKS_END, 1)
                new_content = pre.rstrip() + "\n\n" + block + post.lstrip()
            else:
                new_content = content.rstrip() + "\n\n" + block

            if new_content != content:
                tmp = target.with_suffix(target.suffix + f".tmp.{int(time.time())}")
                tmp.write_text(new_content, encoding="utf-8")
                tmp.replace(target)
                updated += 1

        return updated

    def build_context(self, query: str) -> str:
        if not self._cfg.enabled:
            return ""

        q = (query or "").strip().lower()
        if not q:
            return ""

        words = [w for w in re.findall(r"[a-z0-9_-]{3,}", q) if w not in {"the", "and", "for", "with"}]
        if not words:
            return ""

        scored: list[tuple[int, Path, str]] = []
        for p in self._iter_notes():
            body = p.read_text(encoding="utf-8", errors="replace")
            body_l = body.lower()
            score = sum(body_l.count(w) for w in words)
            if score <= 0:
                continue
            snippet = body.strip().replace("\n", " ")
            if len(snippet) > self._cfg.snippet_chars:
                snippet = snippet[: self._cfg.snippet_chars] + "…"
            scored.append((score, p, snippet))

        if not scored:
            return ""

        scored.sort(key=lambda t: (-t[0], t[1].as_posix().lower()))
        top = scored[: max(1, self._cfg.max_snippets)]

        lines = ["Memory notes (local markdown):"]
        for score, p, snippet in top:
            rel = _relative_link(self._cfg.dir / "_.md", p)
            lines.append(f"- {p.stem} ({score} hits): {rel}")
            lines.append(f"  {snippet}")
        return "\n".join(lines).strip()

