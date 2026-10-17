"""Mojang profile lookup: nicknames must point at real Java accounts.
Uses the official HTTPS endpoint; falls back gracefully when offline."""
import logging
from typing import Optional

log = logging.getLogger(__name__)

LOOKUP_URL = "https://api.minecraftservices.com/minecraft/profile/lookup/name/{name}"

_NICK_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


def nickname_format_ok(nickname: str) -> bool:
    return (
        3 <= len(nickname) <= 16
        and all(c in _NICK_CHARS for c in nickname)
    )


async def lookup_profile(client, nickname: str) -> Optional[tuple[str, str]]:
    """Return (nickname_correct_case, uuid_no_dashes) or None.

    Raises RuntimeError on unexpected upstream trouble so callers can decide
    whether to soft-fail (registration UX) or hard-log (audit trail).
    """
    if not nickname_format_ok(nickname):
        return None
    resp = await client.get(LOOKUP_URL.format(name=nickname))
    if resp.status_code == 200:
        body = resp.json()
        return body["name"], body["id"]
    if resp.status_code == 404:
        return None
    log.warning("mojang lookup %s -> %s", nickname, resp.status_code)
    raise RuntimeError(f"Mojang API responded {resp.status_code}")
