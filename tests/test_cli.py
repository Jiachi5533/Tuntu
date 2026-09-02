from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tuntu.auth import AuthService
from tuntu.cli import main
from tuntu.db import Database


class CliTests(unittest.TestCase):
    def test_reset_setup_token_prints_only_a_short_lived_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch.dict(
                "os.environ",
                {"TUNTU_DATA_DIR": temporary},
                clear=False,
            ), redirect_stdout(output):
                self.assertEqual(main(["reset-setup-token"]), 0)
            rendered = output.getvalue()
            self.assertIn("Setup Token", rendered)
            self.assertIn("前有效", rendered)
            self.assertNotIn("password", rendered.casefold())

            raw_token = rendered.rsplit("：", 1)[1].strip()
            database = Database(Path(temporary) / "tuntu.db")
            try:
                user = AuthService(database).consume_setup_token(
                    raw_token,
                    "admin",
                    "correct-horse-battery-staple",
                )
            finally:
                database.dispose()
            self.assertEqual(user.username, "admin")


if __name__ == "__main__":
    unittest.main()
