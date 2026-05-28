from __future__ import annotations

import unittest
from unittest.mock import patch

from nudge_webhook.db import init_and_migrate


class TestDbVercelTmpFallback(unittest.TestCase):
    def test_init_and_migrate_falls_back_to_tmp_on_readonly_parent(self) -> None:
        def raise_readonly(*args, **kwargs):
            _ = (args, kwargs)
            raise OSError(30, "Read-only file system")

        with patch("os.makedirs", side_effect=raise_readonly):
            info = init_and_migrate("/var/task/data/nudge.sqlite3")
            self.assertTrue(str(info.path).startswith("/tmp/"))


if __name__ == "__main__":
    unittest.main()

