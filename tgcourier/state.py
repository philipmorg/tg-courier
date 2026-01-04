from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    text: str
    ts_ms: int


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": 1, "claimed_user_id": None, "chats": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            backup = self._path.with_suffix(self._path.suffix + f".corrupt.{int(time.time())}")
            self._path.replace(backup)
            return {"version": 1, "claimed_user_id": None, "chats": {}}

    def save(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def get_claimed_user_id(self) -> int | None:
        data = self.load()
        v = data.get("claimed_user_id")
        return int(v) if isinstance(v, int) else None

    def set_claimed_user_id(self, user_id: int) -> None:
        data = self.load()
        data["claimed_user_id"] = int(user_id)
        self.save(data)

    def reset_chat(self, chat_id: int) -> None:
        data = self.load()
        chats = data.setdefault("chats", {})
        chats[str(chat_id)] = {
            "created_at_ms": int(time.time() * 1000),
            "updated_at_ms": int(time.time() * 1000),
            "messages": [],
            "prefs": {},
        }
        self.save(data)

    def get_pref(self, chat_id: int, key: str, default: object | None = None) -> object | None:
        data = self.load()
        chat = (data.get("chats") or {}).get(str(chat_id)) or {}
        prefs = chat.get("prefs") or {}
        if not isinstance(prefs, dict):
            return default
        return prefs.get(key, default)

    def set_pref(self, chat_id: int, key: str, value: object) -> None:
        data = self.load()
        chats = data.setdefault("chats", {})
        chat = chats.get(str(chat_id))
        if not chat:
            chat = {
                "created_at_ms": int(time.time() * 1000),
                "updated_at_ms": int(time.time() * 1000),
                "messages": [],
                "prefs": {},
            }
            chats[str(chat_id)] = chat

        prefs = chat.get("prefs")
        if not isinstance(prefs, dict):
            prefs = {}
            chat["prefs"] = prefs
        prefs[key] = value
        chat["updated_at_ms"] = int(time.time() * 1000)
        self.save(data)

    def append(self, chat_id: int, role: str, text: str) -> None:
        data = self.load()
        chats = data.setdefault("chats", {})
        chat = chats.get(str(chat_id))
        if not chat:
            chat = {
                "created_at_ms": int(time.time() * 1000),
                "updated_at_ms": int(time.time() * 1000),
                "messages": [],
                "prefs": {},
            }
            chats[str(chat_id)] = chat

        chat["updated_at_ms"] = int(time.time() * 1000)
        chat.setdefault("messages", []).append(
            {"role": role, "text": text, "ts_ms": int(time.time() * 1000)}
        )
        self.save(data)

    def get_messages(self, chat_id: int, max_turns: int) -> list[ChatMessage]:
        data = self.load()
        chat = (data.get("chats") or {}).get(str(chat_id)) or {}
        raw_messages = chat.get("messages") or []
        msgs = [
            ChatMessage(
                role=str(m.get("role") or ""),
                text=str(m.get("text") or ""),
                ts_ms=int(m.get("ts_ms") or 0),
            )
            for m in raw_messages
            if isinstance(m, dict)
        ]

        keep = max(0, int(max_turns)) * 2
        if keep and len(msgs) > keep:
            msgs = msgs[-keep:]
        return msgs


def render_prompt(system_prompt: str, messages: list[ChatMessage], user_text: str) -> str:
    parts: list[str] = []
    if system_prompt.strip():
        parts.append(system_prompt.strip())
        parts.append("")

    for m in messages:
        if m.role == "user":
            parts.append(f"User: {m.text}".rstrip())
        elif m.role == "assistant":
            parts.append(f"Assistant: {m.text}".rstrip())
        else:
            continue
        parts.append("")

    parts.append(f"User: {user_text}".rstrip())
    parts.append("")
    parts.append("Assistant:")
    return "\n".join(parts).strip() + "\n"
