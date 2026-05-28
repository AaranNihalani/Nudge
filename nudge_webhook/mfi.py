from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import click
from flask import Blueprint, Flask, Response, current_app, jsonify, request

from .db import connect


@dataclass(frozen=True)
class MfiRate:
    district: str
    lender: str
    rate_apr: float
    effective_date: str | None = None
    source: str | None = None


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _to_rate(value: Any) -> float:
    raw = _to_text(value)
    if raw == "":
        raise ValueError("missing rate_apr")
    rate = float(raw)
    if not (rate > 0):
        raise ValueError("rate_apr must be > 0")
    return rate


def _read_csv(path: str) -> list[MfiRate]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows: list[MfiRate] = []
        for i, row in enumerate(reader, start=2):
            district = _to_text(row.get("district"))
            lender = _to_text(row.get("lender") or row.get("lender_name"))
            if district == "" or lender == "":
                raise ValueError(f"invalid row at line {i}")
            rows.append(
                MfiRate(
                    district=district,
                    lender=lender,
                    rate_apr=_to_rate(row.get("rate_apr") or row.get("rate")),
                    effective_date=_to_text(row.get("effective_date")) or None,
                    source=_to_text(row.get("source")) or None,
                )
            )
        return rows


def _read_json(path: str) -> list[MfiRate]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, list):
        raise ValueError("JSON dataset must be a list")

    rows: list[MfiRate] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("JSON dataset items must be objects")
        district = _to_text(item.get("district"))
        lender = _to_text(item.get("lender") or item.get("lender_name"))
        if district == "" or lender == "":
            raise ValueError("invalid JSON dataset row")
        rows.append(
            MfiRate(
                district=district,
                lender=lender,
                rate_apr=_to_rate(item.get("rate_apr") or item.get("rate")),
                effective_date=_to_text(item.get("effective_date")) or None,
                source=_to_text(item.get("source")) or None,
            )
        )
    return rows


def read_dataset(dataset_path: str) -> list[MfiRate]:
    suffix = Path(dataset_path).suffix.lower()
    if suffix == ".json":
        return _read_json(dataset_path)
    return _read_csv(dataset_path)


def load_dataset_into_sqlite(db_path: str, dataset_path: str, *, replace: bool = True) -> int:
    rows = read_dataset(dataset_path)
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if replace:
            conn.execute("DELETE FROM mfi_rates")
            conn.execute("DELETE FROM mfi_districts")
            conn.execute("DELETE FROM mfi_lenders")

        for district in sorted({r.district for r in rows}):
            conn.execute("INSERT OR IGNORE INTO mfi_districts(name) VALUES (?)", (district,))

        for lender in sorted({r.lender for r in rows}):
            conn.execute("INSERT OR IGNORE INTO mfi_lenders(name) VALUES (?)", (lender,))

        district_id_by_name = {
            str(r["name"]): int(r["id"])
            for r in conn.execute("SELECT id, name FROM mfi_districts").fetchall()
        }
        lender_id_by_name = {
            str(r["name"]): int(r["id"])
            for r in conn.execute("SELECT id, name FROM mfi_lenders").fetchall()
        }

        stmt = """
            INSERT INTO mfi_rates(district_id, lender_id, rate_apr, effective_date, source, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(district_id, lender_id) DO UPDATE SET
                rate_apr=excluded.rate_apr,
                effective_date=excluded.effective_date,
                source=excluded.source,
                updated_at=CURRENT_TIMESTAMP
        """
        for r in rows:
            conn.execute(
                stmt,
                (
                    district_id_by_name[r.district],
                    lender_id_by_name[r.lender],
                    r.rate_apr,
                    r.effective_date,
                    r.source,
                ),
            )

        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_districts(db_path: str) -> list[str]:
    conn = connect(db_path)
    try:
        return [
            str(r["name"])
            for r in conn.execute("SELECT name FROM mfi_districts ORDER BY name COLLATE NOCASE ASC").fetchall()
        ]
    finally:
        conn.close()


def query_by_district(db_path: str, district: str) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT d.name AS district, l.name AS lender, r.rate_apr, r.effective_date, r.source
            FROM mfi_rates r
            JOIN mfi_districts d ON d.id = r.district_id
            JOIN mfi_lenders l ON l.id = r.lender_id
            WHERE d.name = ?
            ORDER BY r.rate_apr ASC, l.name COLLATE NOCASE ASC, l.id ASC
            """,
            (district,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def query_by_rate_range(
    db_path: str,
    *,
    min_rate: float | None = None,
    max_rate: float | None = None,
    district: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if district:
        clauses.append("d.name = ?")
        params.append(district)
    if min_rate is not None:
        clauses.append("r.rate_apr >= ?")
        params.append(float(min_rate))
    if max_rate is not None:
        clauses.append("r.rate_apr <= ?")
        params.append(float(max_rate))

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT d.name AS district, l.name AS lender, r.rate_apr, r.effective_date, r.source
            FROM mfi_rates r
            JOIN mfi_districts d ON d.id = r.district_id
            JOIN mfi_lenders l ON l.id = r.lender_id
            {where_sql}
            ORDER BY r.rate_apr ASC, l.name COLLATE NOCASE ASC, l.id ASC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def query_top_n_alternatives(
    db_path: str,
    *,
    district: str,
    current_rate: float | None = None,
    n: int = 3,
    exclude_lender: str | None = None,
    include_equal: bool = False,
) -> list[dict[str, Any]]:
    op = "<=" if include_equal else "<"
    params: list[Any] = [district]
    rate_sql = ""
    if current_rate is not None:
        rate_sql = f"AND r.rate_apr {op} ?"
        params.append(float(current_rate))
    exclude_sql = ""
    if exclude_lender:
        exclude_sql = "AND l.name <> ?"
        params.append(exclude_lender)

    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT d.name AS district, l.name AS lender, r.rate_apr, r.effective_date, r.source
            FROM mfi_rates r
            JOIN mfi_districts d ON d.id = r.district_id
            JOIN mfi_lenders l ON l.id = r.lender_id
            WHERE d.name = ?
                {rate_sql}
                {exclude_sql}
            ORDER BY r.rate_apr ASC, l.name COLLATE NOCASE ASC, l.id ASC
            LIMIT ?
            """,
            params + [int(n)],
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@dataclass(frozen=True)
class MfiRepository:
    db_path: str

    def load(self, dataset_path: str, *, replace: bool = True) -> int:
        return load_dataset_into_sqlite(self.db_path, dataset_path, replace=replace)

    def districts(self) -> list[str]:
        return list_districts(self.db_path)

    def by_district(self, district: str) -> list[dict[str, Any]]:
        return query_by_district(self.db_path, district)

    def by_rate_range(
        self, *, min_rate: float | None = None, max_rate: float | None = None, district: str | None = None
    ) -> list[dict[str, Any]]:
        return query_by_rate_range(self.db_path, min_rate=min_rate, max_rate=max_rate, district=district)

    def top_alternatives(
        self,
        *,
        district: str,
        current_rate: float | None = None,
        n: int = 3,
        exclude_lender: str | None = None,
        include_equal: bool = False,
    ) -> list[dict[str, Any]]:
        return query_top_n_alternatives(
            self.db_path,
            district=district,
            current_rate=current_rate,
            n=n,
            exclude_lender=exclude_lender,
            include_equal=include_equal,
        )


def _get_repo() -> MfiRepository:
    repo = current_app.extensions.get("mfi_repo")
    if isinstance(repo, MfiRepository):
        return repo
    raise RuntimeError("MFI repository not configured")


_bp = Blueprint("mfi", __name__, url_prefix="/mfi")


@_bp.get("/districts")
def districts_endpoint() -> Response:
    return jsonify({"districts": _get_repo().districts()})


@_bp.get("/rates")
def rates_endpoint() -> Response:
    district = _to_text(request.args.get("district")) or None
    min_rate_raw = _to_text(request.args.get("min_rate")) or None
    max_rate_raw = _to_text(request.args.get("max_rate")) or None
    min_rate = float(min_rate_raw) if min_rate_raw is not None else None
    max_rate = float(max_rate_raw) if max_rate_raw is not None else None
    return jsonify({"results": _get_repo().by_rate_range(min_rate=min_rate, max_rate=max_rate, district=district)})


@_bp.get("/alternatives")
def alternatives_endpoint() -> Response:
    district = _to_text(request.args.get("district"))
    current_rate_raw = _to_text(request.args.get("current_rate"))
    current_rate = float(current_rate_raw) if current_rate_raw != "" else None
    n = int(_to_text(request.args.get("n") or "3"))
    exclude_lender = _to_text(request.args.get("exclude_lender")) or None
    include_equal = (_to_text(request.args.get("include_equal")) or "false").lower() in {"1", "true", "yes"}
    return jsonify(
        {
            "results": _get_repo().top_alternatives(
                district=district,
                current_rate=current_rate,
                n=n,
                exclude_lender=exclude_lender,
                include_equal=include_equal,
            )
        }
    )


def register_mfi(app: Flask) -> None:
    repo = MfiRepository(db_path=app.config["NUDGE_DB"].path)
    app.extensions["mfi_repo"] = repo
    app.register_blueprint(_bp)

    default_dataset = os.environ.get("NUDGE_MFI_DATASET_PATH") or str(
        Path(__file__).resolve().parents[1] / "datasets" / "mfi_rates.csv"
    )

    @app.cli.command("load-mfi")
    @click.option("--dataset", "dataset_path", default=default_dataset, show_default=True)
    @click.option("--replace/--no-replace", default=True, show_default=True)
    def load_mfi_command(dataset_path: str, replace: bool) -> None:
        count = repo.load(dataset_path, replace=replace)
        click.echo(f"loaded {count} rows")
