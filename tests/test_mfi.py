from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nudge_webhook.db import init_and_migrate
from nudge_webhook.mfi import load_dataset_into_sqlite, query_by_district, query_top_n_alternatives
from nudge_webhook.mfi import list_districts


def _write_test_dataset_csv(path: str) -> None:
    Path(path).write_text(
        "\n".join(
            [
                "district,lender,rate_apr,effective_date,source",
                "Kampala,GreenField Finance,20.5,2025-01-01,test",
                "Kampala,Sunrise MFI,18.0,2025-01-01,test",
                "Kampala,Unity Credit,18.0,2025-01-01,test",
                "Gulu,GreenField Finance,19.5,2025-01-01,test",
                "Gulu,RiverBank Microcredit,19.5,2025-01-01,test",
                "Gulu,Valley Lending,22.0,2025-01-01,test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class TestMfi(unittest.TestCase):
    def test_loader_and_queries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            dataset_path = str(Path(td) / "mfi_rates_test.csv")
            _write_test_dataset_csv(dataset_path)
            init_and_migrate(db_path)
            load_dataset_into_sqlite(db_path, dataset_path, replace=True)

            self.assertEqual(list_districts(db_path), ["Gulu", "Kampala"])

            gulu = query_by_district(db_path, "Gulu")
            self.assertEqual([r["rate_apr"] for r in gulu], [19.5, 19.5, 22.0])
            self.assertEqual([r["lender"] for r in gulu[:2]], ["GreenField Finance", "RiverBank Microcredit"])

            alternatives = query_top_n_alternatives(
                db_path, district="Kampala", current_rate=20.5, n=10, exclude_lender="GreenField Finance"
            )
            self.assertEqual([r["rate_apr"] for r in alternatives], [18.0, 18.0])
            self.assertEqual([r["lender"] for r in alternatives], ["Sunrise MFI", "Unity Credit"])


if __name__ == "__main__":
    unittest.main()
