"""Invite codes (data-model.md): 8 characters without look-alikes, shown as K7QM-4XPA."""

import re
import secrets

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
_CODE = re.compile(f"^[{ALPHABET}]{{8}}$")


def normalise(text: str | None) -> str | None:
    """What the player typed (any case, with or without the dash) -> the stored code, or None."""
    if not text:
        return None
    code = re.sub(r"[\s-]", "", text).upper()
    return code if _CODE.match(code) else None


def shown(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))
