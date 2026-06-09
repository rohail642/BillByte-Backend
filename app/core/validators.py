"""Shared input validators (password policy, etc.)."""
import re

# A small set of obviously-weak passwords to reject outright.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwerty123", "qwertyuiop", "11111111", "00000000",
    "iloveyou", "admin123", "letmein1", "welcome1", "abc12345",
    "billbyte", "billbyte1", "restaurant",
}

_MIN_LENGTH = 8


def validate_password(password: str) -> str:
    """Enforce the password policy. Returns the password unchanged if valid,
    otherwise raises ValueError (Pydantic surfaces this as a 422).

    Policy: >= 8 chars, at least one lowercase, one uppercase, one digit, and
    not on the common-password blocklist.
    """
    if password is None or len(password) < _MIN_LENGTH:
        raise ValueError(f"Password must be at least {_MIN_LENGTH} characters long.")
    if password.lower() in _COMMON_PASSWORDS:
        raise ValueError("Password is too common. Please choose a stronger one.")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter.")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one digit.")
    return password
