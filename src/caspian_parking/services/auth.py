"""Users, passwords (scrypt) and shift sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from caspian_parking.core.permissions import BUILTIN_PRESETS, normalize_permissions
from caspian_parking.data.models import User
from caspian_parking.data.models.system import ShiftEvent
from caspian_parking.data.repositories.system import ShiftRepository, UserRepository

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
MIN_PASSWORD_LENGTH = 6


class AuthError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES)
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    actual = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def validate_new_password(password: str, min_length: int = MIN_PASSWORD_LENGTH) -> None:
    if len(password) < min_length:
        raise AuthError("password.too_short")


@dataclass(frozen=True)
class CurrentUser:
    """The logged-in operator as the UI sees it."""

    id: str
    username: str
    display_name: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    theme: str | None = None
    must_change_password: bool = False

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @classmethod
    def from_user(cls, user: User) -> CurrentUser:
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            permissions=frozenset(user.permissions or ()),
            theme=user.theme,
            must_change_password=user.must_change_password,
        )


def create_user(
    session: Session,
    username: str,
    display_name: str,
    password: str,
    preset: str | None = None,
    permissions: list[str] | None = None,
    must_change_password: bool = False,
) -> User:
    repo = UserRepository(session)
    username = username.strip().lower()
    if not username:
        raise AuthError("user.username_required")
    if repo.by_username(username) is not None:
        raise AuthError("user.username_taken")
    validate_new_password(password)
    perms = permissions if permissions is not None else sorted(BUILTIN_PRESETS.get(preset or "", frozenset()))
    return repo.add(
        User(
            username=username,
            display_name=display_name.strip() or username,
            password_hash=hash_password(password),
            permissions=normalize_permissions(perms),
            role_preset_code=preset,
            must_change_password=must_change_password,
        )
    )


def authenticate(session: Session, username: str, password: str) -> User | None:
    """Return the active user for valid credentials, otherwise None (constant-ish time)."""
    user = UserRepository(session).by_username(username)
    if user is None or not user.is_active:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def change_password(session: Session, user: User, new_password: str) -> None:
    validate_new_password(new_password)
    UserRepository(session).update(user, password_hash=hash_password(new_password), must_change_password=False)


def start_shift(session: Session, user_id: str, gate_code: int | None) -> ShiftEvent:
    return ShiftRepository(session).append(ShiftEvent(user_id=user_id, kind="login", gate_code=gate_code))


def end_shift(session: Session, user_id: str, gate_code: int | None) -> ShiftEvent:
    return ShiftRepository(session).append(ShiftEvent(user_id=user_id, kind="logout", gate_code=gate_code))
