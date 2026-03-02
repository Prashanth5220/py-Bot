"""
crypto.py — password hashing, OTP generation, salt handling.

Java equivalent: a utility class with static methods (like BCryptPasswordEncoder).
Python's hashlib is the equivalent of Java's MessageDigest.
"""
import hashlib
import hmac
import random
import secrets
import string


# ── OTP ───────────────────────────────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """Generate a numeric OTP (e.g., '847291')."""
    return "".join(random.choices(string.digits, k=length))


# ── Salt & Hash ───────────────────────────────────────────────────────────────

def generate_salt(length: int = 16) -> str:
    """Generate a cryptographically secure random hex salt."""
    return secrets.token_hex(length)


def hash_with_salt(password: str, salt: str) -> str:
    """
    SHA-256 hash of (salt + password).
    Java equivalent: MessageDigest.getInstance("SHA-256")
    """
    combined = (salt + password).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def check_password(input_password: str, stored_hash: str, salt: str | None) -> bool:
    """
    Verify a password.
    - New users: salt-based SHA-256
    - Legacy users (no salt): plain SHA-256 (backward compatible)
    """
    if salt:
        return hash_with_salt(input_password, salt) == stored_hash
    # Legacy: plain SHA-256 (no salt)
    return hashlib.sha256(input_password.encode()).hexdigest() == stored_hash


# ── Password strength ─────────────────────────────────────────────────────────

def is_strong_password(password: str) -> bool:
    """
    Requires: 8+ chars, uppercase, lowercase, digit, special char.
    Java equivalent: regex pattern check.
    """
    import re
    pattern = r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@$!%*?&]).{8,}$"
    return bool(re.match(pattern, password))


# ── UUID ──────────────────────────────────────────────────────────────────────

def new_uuid() -> str:
    """Generate a random UUID string. Java: UUID.randomUUID().toString()"""
    import uuid
    return str(uuid.uuid4())
