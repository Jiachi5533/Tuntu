from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from tuntu.config import StartupConfig
from tuntu.logging_setup import SecretRedactionFilter, configure_logging, redact_text


class LoggingTests(unittest.TestCase):
    def test_sensitive_headers_and_fields_are_redacted(self):
        source = (
            "Authorization: Bearer bearer-secret Cookie: session=secret; other=value "
            "password=hunter2 cd2_api_token=token-secret"
        )
        result = redact_text(source)
        for secret in (
            "bearer-secret",
            "session=secret",
            "other=value",
            "hunter2",
            "token-secret",
        ):
            self.assertNotIn(secret, result)
        self.assertEqual(result.count("<redacted>"), 4)

    def test_json_and_python_mapping_secret_values_are_redacted(self):
        source = (
            '{"Authorization": "Bearer json-secret", '
            '"Cookie": "session=json-cookie; other=value", '
            '"password": "json-password", '
            "'api_token': 'mapping-token'}"
        )

        result = redact_text(source)

        for secret in (
            "json-secret",
            "json-cookie",
            "other=value",
            "json-password",
            "mapping-token",
        ):
            self.assertNotIn(secret, result)
        self.assertEqual(result.count("<redacted>"), 4)

    def test_filter_never_redacts_the_deliberate_setup_token_log_label(self):
        record = logging.LogRecord(
            "tuntu", logging.WARNING, __file__, 1, "Setup Token：fixture", (), None
        )
        SecretRedactionFilter().filter(record)
        self.assertIn("fixture", record.getMessage())

    def test_rotating_log_has_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = StartupConfig(data_dir=Path(temporary))
            configure_logging(config)
            mode = (config.log_dir / "tuntu.log").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
