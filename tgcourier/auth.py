from __future__ import annotations

from telegram import Update

from .config import Settings
from .state import StateStore


def resolve_allowed_user_id(settings: Settings, store: StateStore) -> int | None:
    if settings.allowed_user_id is not None:
        return settings.allowed_user_id
    return store.get_claimed_user_id()


def resolve_allowed_username(settings: Settings) -> str | None:
    return settings.allowed_username.lower() if settings.allowed_username else None


def is_allowed_user(settings: Settings, store: StateStore, update: Update) -> bool:
    if not update.effective_user:
        return False

    allowed_user_id = resolve_allowed_user_id(settings, store)
    if allowed_user_id is not None:
        return update.effective_user.id == allowed_user_id

    allowed_username = resolve_allowed_username(settings)
    if allowed_username:
        username = (update.effective_user.username or "").strip().lower()
        return bool(username) and username == allowed_username

    return False

