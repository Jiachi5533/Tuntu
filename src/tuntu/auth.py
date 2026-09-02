from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy import func, select, update

from tuntu.db.database import Database
from tuntu.db.models import SessionRow, SetupTokenRow, UserRow


_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")


class AuthError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class SecretGrant:
    token: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"SecretGrant(token=<redacted>, expires_at={self.expires_at!r})"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: int
    username: str


@dataclass(frozen=True, slots=True)
class BootstrapStatus:
    initialized: bool
    setup_token_active: bool


class AuthService:
    def __init__(
        self,
        database: Database,
        *,
        setup_token_ttl: timedelta = timedelta(minutes=30),
        session_ttl: timedelta = timedelta(days=7),
        now=None,
        password_hasher: PasswordHasher | None = None,
    ):
        if setup_token_ttl.total_seconds() <= 0 or session_ttl.total_seconds() <= 0:
            raise ValueError("auth TTLs must be positive")
        self.database = database
        self.setup_token_ttl = setup_token_ttl
        self.session_ttl = session_ttl
        self._now = now or (lambda: datetime.now(UTC))
        self._password_hasher = password_hasher or PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=2,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    def bootstrap_status(self) -> BootstrapStatus:
        now = self._now()
        with self.database.session() as session:
            initialized = bool(
                session.scalar(select(func.count()).select_from(UserRow))
            )
            active = session.scalar(
                select(SetupTokenRow.id).where(
                    SetupTokenRow.used_at.is_(None),
                    SetupTokenRow.expires_at > now,
                )
            )
            return BootstrapStatus(initialized, active is not None)

    def bootstrap_if_needed(self) -> SecretGrant | None:
        status = self.bootstrap_status()
        if status.initialized or status.setup_token_active:
            return None
        return self.rotate_setup_token()

    def rotate_setup_token(self) -> SecretGrant:
        now = self._now()
        raw = secrets.token_urlsafe(32)
        expires_at = now + self.setup_token_ttl
        with self.database.session() as session:
            session.execute(
                update(SetupTokenRow)
                .where(SetupTokenRow.used_at.is_(None))
                .values(used_at=now)
            )
            session.add(
                SetupTokenRow(
                    token_hash=self._token_hash(raw),
                    expires_at=expires_at,
                )
            )
        return SecretGrant(raw, expires_at)

    def consume_setup_token(
        self, token: str, username: str, password: str
    ) -> AuthenticatedUser:
        normalized_username = self._validate_username(username)
        self._validate_password(password)
        password_hash = self._password_hasher.hash(password)
        token_hash = self._token_hash(token)
        now = self._now()
        with self.database.session() as session:
            claimed = session.execute(
                update(SetupTokenRow)
                .where(
                    SetupTokenRow.token_hash == token_hash,
                    SetupTokenRow.used_at.is_(None),
                    SetupTokenRow.expires_at > now,
                )
                .values(used_at=now)
            )
            if claimed.rowcount != 1:
                raise AuthError("setup_token_invalid")

            users = session.scalars(select(UserRow).order_by(UserRow.id)).all()
            if users:
                user = users[0]
                if user.username.casefold() != normalized_username.casefold():
                    raise AuthError("setup_username_mismatch")
                user.password_hash = password_hash
                user.session_version += 1
                session.execute(
                    update(SessionRow)
                    .where(SessionRow.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
            else:
                user = UserRow(
                    username=normalized_username,
                    password_hash=password_hash,
                )
                session.add(user)
                session.flush()
            return AuthenticatedUser(user.id, user.username)

    def login(self, username: str, password: str) -> SecretGrant:
        normalized = username.strip().casefold()
        with self.database.session() as session:
            user = session.scalar(
                select(UserRow).where(func.lower(UserRow.username) == normalized)
            )
            if user is None or not self._verify_password(user.password_hash, password):
                raise AuthError("invalid_credentials")
            if self._password_hasher.check_needs_rehash(user.password_hash):
                user.password_hash = self._password_hasher.hash(password)
            raw = secrets.token_urlsafe(32)
            expires_at = self._now() + self.session_ttl
            session.add(
                SessionRow(
                    id=secrets.token_hex(16),
                    user_id=user.id,
                    token_hash=self._token_hash(raw),
                    expires_at=expires_at,
                )
            )
            return SecretGrant(raw, expires_at)

    def authenticate(self, token: str | None) -> AuthenticatedUser:
        if not token:
            raise AuthError("authentication_required")
        now = self._now()
        token_hash = self._token_hash(token)
        with self.database.session() as session:
            result = session.execute(
                select(SessionRow, UserRow)
                .join(UserRow, UserRow.id == SessionRow.user_id)
                .where(SessionRow.token_hash == token_hash)
            ).one_or_none()
            if result is None:
                raise AuthError("authentication_required")
            session_row, user = result
            expires_at = self._as_utc(session_row.expires_at)
            if session_row.revoked_at is not None or expires_at <= now:
                if session_row.revoked_at is None:
                    session_row.revoked_at = now
                raise AuthError("authentication_required")
            session_row.expires_at = now + self.session_ttl
            return AuthenticatedUser(user.id, user.username)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        now = self._now()
        with self.database.session() as session:
            session.execute(
                update(SessionRow)
                .where(
                    SessionRow.token_hash == self._token_hash(token),
                    SessionRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )

    def change_password(
        self, token: str, current_password: str, new_password: str
    ) -> None:
        user_record = self.authenticate(token)
        self._validate_password(new_password)
        new_hash = self._password_hasher.hash(new_password)
        now = self._now()
        with self.database.session() as session:
            user = session.get(UserRow, user_record.id)
            if user is None or not self._verify_password(
                user.password_hash, current_password
            ):
                raise AuthError("invalid_credentials")
            user.password_hash = new_hash
            user.session_version += 1
            session.execute(
                update(SessionRow)
                .where(
                    SessionRow.user_id == user.id,
                    SessionRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_username(username: str) -> str:
        value = username.strip()
        if not _USERNAME.fullmatch(value):
            raise AuthError("invalid_username")
        return value

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12 or len(password) > 256:
            raise AuthError("invalid_password")

    def _verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return self._password_hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
