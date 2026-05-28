from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from nudge_webhook.db import init_and_migrate


class TestDbVercelTmpFallback(unittest.TestCase):
    def test_init_and_migrate_falls_back_to_tmp_on_readonly_parent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test.sqlite3")
            init_and_migrate(db_path)

        def raise_readonly(*args, **kwargs):
            _ = (args, kwargs)
            raise OSError(30, "Read-only file system")

        with patch("os.makedirs", side_effect=raise_readonly):
            info = init_and_migrate("/var/task/data/nudge.sqlite3")
            self.assertTrue(str(info.path).startswith("/tmp/"))


if __name__ == "__main__":
    unittest.main()

