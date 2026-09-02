from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy import select

from tuntu.auth import AuthError, AuthService
from tuntu.config import StartupConfig
from tuntu.db import Database
from tuntu.db.migration import migrate_database
from tuntu.db.models import SessionRow, SetupTokenRow, UserRow


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 13, tzinfo=UTC)

    def __call__(self):
        return self.value


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "tuntu.db"
        migrate_database(self.db_path, self.root / "backups")
        self.database = Database(self.db_path)
        self.clock = MutableClock()
        self.auth = AuthService(
            self.database,
            setup_token_ttl=timedelta(minutes=10),
            session_ttl=timedelta(days=7),
            now=self.clock,
            password_hasher=PasswordHasher(
                time_cost=1,
                memory_cost=8_192,
                parallelism=1,
                hash_len=16,
                salt_len=8,
                type=Type.ID,
            ),
        )

    def tearDown(self):
        self.database.dispose()
        self.temp_dir.cleanup()

    def initialize(self):
        grant = self.auth.bootstrap_if_needed()
        self.assertIsNotNone(grant)
        user = self.auth.consume_setup_token(
            grant.token, "admin", "correct horse battery"
        )
        return user

    def test_fresh_boot_emits_setup_token_once_and_only_hash_is_persisted(self):
        grant = self.auth.bootstrap_if_needed()

        self.assertIsNotNone(grant)
        self.assertNotIn(grant.token, repr(grant))
        self.assertIsNone(self.auth.bootstrap_if_needed())
        with self.database.session() as session:
            row = session.scalar(select(SetupTokenRow))
            self.assertNotEqual(row.token_hash, grant.token)
            self.assertEqual(len(row.token_hash), 64)

    def test_setup_token_is_single_use_and_password_is_argon2id(self):
        grant = self.auth.bootstrap_if_needed()
        user = self.auth.consume_setup_token(
            grant.token, "admin", "correct horse battery"
        )

        self.assertEqual(user.username, "admin")
        with self.database.session() as session:
            row = session.get(UserRow, user.id)
            self.assertTrue(row.password_hash.startswith("$argon2id$"))
        with self.assertRaisesRegex(AuthError, "setup_token_invalid"):
            self.auth.consume_setup_token(
                grant.token, "admin", "another valid password"
            )

    def test_login_session_slides_expires_and_logout_revokes(self):
        self.initialize()
        grant = self.auth.login("ADMIN", "correct horse battery")
        original_expiry = grant.expires_at

        self.clock.value += timedelta(days=1)
        user = self.auth.authenticate(grant.token)

        self.assertEqual(user.username, "admin")
        with self.database.session() as session:
            row = session.scalar(select(SessionRow))
            self.assertEqual(
                row.expires_at.replace(tzinfo=UTC), original_expiry + timedelta(days=1)
            )
            self.assertNotEqual(row.token_hash, grant.token)
        self.auth.logout(grant.token)
        with self.assertRaisesRegex(AuthError, "authentication_required"):
            self.auth.authenticate(grant.token)

    def test_expired_setup_and_session_tokens_are_rejected(self):
        setup = self.auth.bootstrap_if_needed()
        self.clock.value += timedelta(minutes=11)
        with self.assertRaisesRegex(AuthError, "setup_token_invalid"):
            self.auth.consume_setup_token(
                setup.token, "admin", "correct horse battery"
            )

        replacement = self.auth.rotate_setup_token()
        self.auth.consume_setup_token(
            replacement.token, "admin", "correct horse battery"
        )
        session = self.auth.login("admin", "correct horse battery")
        self.clock.value += timedelta(days=8)
        with self.assertRaisesRegex(AuthError, "authentication_required"):
            self.auth.authenticate(session.token)

    def test_password_change_revokes_all_sessions(self):
        self.initialize()
        first = self.auth.login("admin", "correct horse battery")
        second = self.auth.login("admin", "correct horse battery")

        self.auth.change_password(
            first.token, "correct horse battery", "new correct horse battery"
        )

        for token in (first.token, second.token):
            with self.assertRaisesRegex(AuthError, "authentication_required"):
                self.auth.authenticate(token)
        with self.assertRaisesRegex(AuthError, "invalid_credentials"):
            self.auth.login("admin", "correct horse battery")
        self.assertIsNotNone(
            self.auth.login("admin", "new correct horse battery")
        )

    def test_rotated_setup_token_resets_existing_admin_without_printing_password(self):
        self.initialize()
        old_session = self.auth.login("admin", "correct horse battery")
        reset = self.auth.rotate_setup_token()

        user = self.auth.consume_setup_token(
            reset.token, "admin", "replacement horse battery"
        )

        self.assertEqual(user.username, "admin")
        with self.assertRaisesRegex(AuthError, "authentication_required"):
            self.auth.authenticate(old_session.token)
        self.assertIsNotNone(
            self.auth.login("admin", "replacement horse battery")
        )

    def test_invalid_credentials_and_inputs_use_stable_codes_without_secrets(self):
        self.initialize()
        for username, password, code in (
            ("admin", "wrong password value", "invalid_credentials"),
            ("unknown", "correct horse battery", "invalid_credentials"),
        ):
            with self.subTest(username=username), self.assertRaisesRegex(
                AuthError, code
            ) as caught:
                self.auth.login(username, password)
            self.assertNotIn(password, str(caught.exception))


class ConfigurationTests(unittest.TestCase):
    def test_startup_config_validates_timezone_and_exposes_nonempty_overrides(self):
        config = StartupConfig(
            data_dir="/tmp/tuntu-fixture",
            timezone="UTC",
            cd2_endpoint="grpc://nas.invalid:19798",
            cd2_api_token="fixture-secret",
            cd2_root="/api-root",
            provider_retries=4,
        )

        self.assertEqual(config.database_path, Path("/tmp/tuntu-fixture/tuntu.db"))
        self.assertEqual(config.runtime_overrides()["cd2_root"], "/api-root")
        self.assertEqual(config.runtime_overrides()["provider_retries"], 4)

    def test_database_and_backup_permissions_are_owner_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            db_path = root / "tuntu.db"
            backup_dir = root / "backups"
            migrate_database(db_path, backup_dir)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(db_path).st_mode & 0o777, 0o600)
