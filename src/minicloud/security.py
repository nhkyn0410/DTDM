import hashlib
import secrets

API_KEY_PREFIX = "mc_"
PREFIX_LENGTH = 12


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw key shown once to the user, lookup prefix, sha256 hash kept in the DB)."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:PREFIX_LENGTH], hash_api_key(raw)


def key_matches(raw: str, key_hash: str) -> bool:
    return secrets.compare_digest(hash_api_key(raw), key_hash)
