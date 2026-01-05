# tg-courier

Tiny Telegram DM → local CLI agent bridge. Single-user. JSON chat history.

Inspired by `https://github.com/clawdbot/clawdbot` (great project) but intentionally much smaller + local-first.

## Requirements

- Python 3.11+ + `uv`
- Telegram bot token (`@BotFather`)
- Optional: `codex` CLI (default agent) or set `AGENT=shell`
- Optional: `peekaboo` (OAuth click-through help)
- Optional: `ffmpeg` + `llm` + `llm-groq-whisper` (speech-to-text)

## Setup

1) Create a Telegram bot with `@BotFather`, copy token.
2) Copy `.env.example` → `.env` and edit:

```bash
TELEGRAM_BOT_TOKEN="123:abc"

# Option A (simple): hard-code your Telegram user id
TELEGRAM_ALLOWED_USER_ID="123456789"

# Option A2: allow by Telegram @username (less stable; usernames can change)
# TELEGRAM_ALLOWED_USER_ID="@yourusername"
# or:
# TELEGRAM_ALLOWED_USERNAME="yourusername"

# Option B (safer onboarding): first user to claim wins
# TELEGRAM_CLAIM_CODE="some-long-random-string"

# Agent: `codex` (default) or `shell`
AGENT="codex"

# Optional: point Codex at a workdir + sandbox
AGENT_WORKDIR="~/Documents/dev"
CODEX_SANDBOX="workspace-write" # or read-only / danger-full-access

# Heartbeat: if an agent turn takes longer than this, bot sends a quiet “working…” ping every N seconds
HEARTBEAT_SEC="45"

# Background jobs: status pings (edit-in-place) every N seconds. 0 disables.
BG_HEARTBEAT_SEC="180"

# Timeout: 0 disables agent timeouts (recommended; heartbeat tells you it’s still alive)
AGENT_TIMEOUT_SEC="0"

# Memory (local markdown notes; Roam-ish `[[wikilinks]]` + backlinks section auto-generated)
MEMORY_ENABLED="1"
MEMORY_DIR="./memory"
MEMORY_AUTO_REBUILD="1"
MEMORY_MAX_SNIPPETS="6"
MEMORY_SNIPPET_CHARS="500"

# Speech-to-text (Telegram voice/audio → Groq Whisper via `llm-groq-whisper`)
STT_ENABLED="1"
STT_MODEL="whisper-large-v3"
STT_LANGUAGE=""          # optional (e.g. "en")
STT_PROMPT=""            # optional spelling guidance
STT_MAX_CHARS="6000"
STT_TIMEOUT_SEC="180"
STT_KEEP_FILES="0"

# OAuth automation (Google): when Codex output includes an accounts.google.com OAuth URL, tg-courier can
# drive the browser via Peekaboo to click through "Next/Continue". "Allow" is off by default.
OAUTH_AUTO_PEEKABOO="1"
OAUTH_AUTO_ALLOW="0"
OAUTH_BROWSER_APP="Google Chrome"
```

3) Run:

```bash
cd tg-courier
./run.py
```

## Local logging

- Always logs to `./data/tg-courier.log` by default.
- `--echo-local` also prints all activity locally.
- `--log <file>` writes to a custom log path.

## Telegram commands

- `/help` basic help
- `/whoami` show user + chat ids
- `/claim <code>` claim bot (only if `TELEGRAM_CLAIM_CODE` set)
- `/reset` wipe chat history for this chat
- `/status` show current settings
- `/bg <command>` run a shell command detached (bot stays usable)
- `/jobs` list background jobs
- `/job <id>` show background job details
- `/job_tail <id>` show last chunk of log
- `/job_cancel <id>` request cancel (SIGTERM; kill after ~10s)
- `/queue` show current + pending jobs
- `/drop` clear pending jobs (keeps current running)
- `/cancel` cancel current job and clear queue
- `/w` codex “yolo” mode on (dangerous)
- `/ro` codex “yolo” mode off
- `/sandbox_rw` codex sandbox to `workspace-write`
- `/sandbox_ro` codex sandbox to `read-only`
- `/mem <text>` append to today’s note
- `/mem_rebuild` rebuild backlinks (“collector” sections)

## Notes

- Codex is invoked via `codex exec` and prompt is rebuilt from the JSON history each turn.
- To use a different CLI, set `AGENT=shell` and `AGENT_CMD="your-command-here"` (prompt on stdin, response on stdout).
- Long runs: the agent can launch detached background jobs via a `TG_COURIER_TOOL: DETACH` directive; tg-courier logs output and notifies Telegram on completion.
- Telegram formatting: bot renders a small Markdown-ish subset to Telegram HTML (`**bold**`, `` `code` ``, ``` ``` blocks, and `[label](https://url)` links). If Telegram rejects formatting, it falls back to plain text.
- Memory: create links like `[[Some Page]]` inside any note in `MEMORY_DIR`; `mem_rebuild` generates a `## Linked references` section in target pages listing backlinks.
- Speech-to-text: send a Telegram voice note or audio file; tg-courier transcribes with `llm groq-whisper` (requires `ffmpeg` + `llm-groq-whisper` plugin).

## Repo hygiene

- `.env`, `data/`, and `memory/` contents are gitignored by default; no secrets checked in.
