"""Idempotency keys: the same input always gets the same key, so a retried write is recognizable."""

import hashlib


def idempotency_key(*parts: str | bytes) -> str:
    # Each part is hashed on its own before they're combined, so ("ab", "c")
    # and ("a", "bc") get different keys even though they concatenate alike.
    combined = hashlib.sha256()
    for part in parts:
        part_bytes = part.encode() if isinstance(part, str) else part
        combined.update(hashlib.sha256(part_bytes).digest())
    return combined.hexdigest()
