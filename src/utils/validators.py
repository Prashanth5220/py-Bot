"""
validators.py — input validation helpers.

Java equivalent: a @Validated / Bean Validation utility class.
"""
import re


def is_valid_indian_phone(phone: str) -> bool:
    """Accepts 10-digit or +91 prefixed Indian mobile numbers (starts 6–9)."""
    return bool(re.match(r"^(\+91)?[6-9]\d{9}$", phone))


def is_valid_email(email: str) -> bool:
    """Basic email format check."""
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email))


def sanitize(text: str, max_len: int = 300) -> str:
    """Strip control characters and trim to max length."""
    cleaned = re.sub(r"[\x00-\x1F\x7F]", "", text).strip()
    return cleaned[:max_len]


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace — for command matching."""
    return re.sub(r"\s+", " ", text.strip().lower())

def is_valid_name(name: str) -> bool:
    """Validates that a name only contains letters, spaces, hyphens, or apostrophes, and is not 'back'."""
    if name.lower().strip() == "back":
        return False
    return bool(re.match(r"^[A-Za-z\s\-']{2,50}$", name))
