from __future__ import annotations

import json


def extract_detach_directive(text: str) -> tuple[dict[str, object] | None, str]:
    """
    Recognizes:
      TG_COURIER_TOOL: DETACH
      { "title": "...", "cmd": "...", "cwd": "..." }

    Returns (spec, cleaned_text).
    """
    raw = text or ""
    lines = raw.splitlines()
    cleaned: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.upper() == "TG_COURIER_TOOL: DETACH":
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines):
                return None, raw
            try:
                spec = json.loads(lines[j])
            except Exception:
                return None, raw
            if not isinstance(spec, dict):
                return None, raw
            cleaned.extend(lines[:i])
            cleaned.extend(lines[j + 1 :])
            return spec, "\n".join(cleaned).strip()
        i += 1

    return None, raw.strip()

