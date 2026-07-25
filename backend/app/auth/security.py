"""Password hashing (F9.1). Argon2id — OWASP's first-choice algorithm
(verified 2026-07-25, cheatsheetseries.owasp.org/cheatsheets/
Password_Storage_Cheat_Sheet.html); the library's default cost
parameters already meet OWASP's minimum baseline, so nothing here is a
tunable an instructor could ever need to change (not a no-hardcoding
rule 7 violation — this is a security engineering constant, not a
business threshold).
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        # VerificationError (incl. VerifyMismatchError) = wrong password;
        # InvalidHashError = a corrupted/foreign hash string. Both mean
        # "not a match" — neither should ever crash the login flow.
        return False
