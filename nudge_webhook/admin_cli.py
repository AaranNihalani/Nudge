from __future__ import annotations

import argparse
from pathlib import Path

from .config import Config
from .daily_runner import run_daily_decisions
from .metrics_export import export_metrics_zip


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    daily = sub.add_parser("run-daily")
    daily.add_argument("--db", default=None)

    export = sub.add_parser("export-metrics")
    export.add_argument("--db", default=None)
    export.add_argument("--out", default="nudge_metrics.zip")

    args = p.parse_args(argv)
    cfg = Config.from_env()
    db_path = str(args.db or cfg.db_path)

    if args.cmd == "run-daily":
        run_daily_decisions(cfg, db_path=db_path)
        return 0

    if args.cmd == "export-metrics":
        bundle = export_metrics_zip(db_path=db_path, anon_salt=cfg.anon_salt)
        out = Path(str(args.out))
        out.write_bytes(bundle.data)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

