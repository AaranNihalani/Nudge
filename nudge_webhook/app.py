from __future__ import annotations

from flask import Flask, Response, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from twilio.request_validator import RequestValidator
    from twilio.twiml.messaging_response import MessagingResponse
except Exception:
    RequestValidator = None

    class MessagingResponse:
        def __init__(self) -> None:
            self._messages: list[str] = []

        def message(self, body: str) -> None:
            self._messages.append(str(body))

        def __str__(self) -> str:
            msg = self._messages[-1] if self._messages else ""
            escaped = (
                msg.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )
            return f"<Response><Message>{escaped}</Message></Response>"

from .bot import InboundMessage, process_twilio_inbound
from .config import Config
from .daily_runner import run_daily_decisions
from .db import connect, init_and_migrate
from .mfi import load_dataset_into_sqlite, register_mfi
from .metrics_export import export_metrics_zip


def create_app(config: Config | None = None) -> Flask:
    cfg = config or Config.from_env()

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.config["NUDGE_CONFIG"] = cfg
    app.config["NUDGE_DB"] = init_and_migrate(cfg.db_path)
    if getattr(cfg, "mfi_autoload", True) and getattr(cfg, "mfi_dataset_path", None):
        db_path = app.config["NUDGE_DB"].path
        conn = connect(db_path)
        try:
            row = conn.execute("SELECT 1 FROM mfi_districts LIMIT 1").fetchone()
        finally:
            conn.close()
        if row is None:
            try:
                load_dataset_into_sqlite(db_path, str(cfg.mfi_dataset_path), replace=True)
            except Exception:
                pass
    register_mfi(app)

    def _admin_ok() -> bool:
        token = cfg.admin_token
        if not token:
            return False
        supplied = request.headers.get("X-Admin-Token") or request.args.get("token") or ""
        return str(supplied) == str(token)

    @app.get("/health")
    def health() -> Response:
        db_info = app.config.get("NUDGE_DB")
        mfi_districts = None
        try:
            conn = connect(app.config["NUDGE_DB"].path)
            try:
                mfi_districts = int(conn.execute("SELECT COUNT(*) AS c FROM mfi_districts").fetchone()["c"])
            finally:
                conn.close()
        except Exception:
            mfi_districts = None
        return jsonify(
            {
                "status": "ok",
                "railway_environment": cfg.railway_environment,
                "db_path": getattr(db_info, "path", None),
                "db_schema_version": getattr(db_info, "schema_version", None),
                "mfi_districts": mfi_districts,
            }
        )

    @app.post("/twilio")
    def twilio_webhook() -> Response:
        if cfg.twilio_validate_signature and cfg.twilio_auth_token and RequestValidator is not None:
            signature = request.headers.get("X-Twilio-Signature", "")
            validator = RequestValidator(cfg.twilio_auth_token)
            if not validator.validate(request.url, request.form, signature):
                return Response("invalid signature", status=403)

        inbound = InboundMessage(
            from_addr=(request.form.get("From") or "").strip(),
            to_addr=(request.form.get("To") or "").strip() or None,
            body=(request.form.get("Body") or "").strip(),
            twilio_message_sid=(request.form.get("MessageSid") or "").strip() or None,
            payload={str(k): str(v) for k, v in request.form.items()},
        )
        db_path = app.config["NUDGE_DB"].path
        reply_text = process_twilio_inbound(cfg, db_path=db_path, inbound=inbound)

        twiml = MessagingResponse()
        twiml.message(reply_text)
        return Response(str(twiml), mimetype="application/xml")

    @app.post("/admin/run-daily")
    def admin_run_daily() -> Response:
        if not _admin_ok():
            return Response("forbidden", status=403)
        db_path = app.config["NUDGE_DB"].path
        result = run_daily_decisions(cfg, db_path=db_path)
        return jsonify(
            {
                "run_date": result.run_date,
                "skipped": result.skipped,
                "evaluated_users": result.evaluated_users,
                "nudges_attempted": result.nudges_attempted,
                "nudges_sent": result.nudges_sent,
                "nudges_failed": result.nudges_failed,
            }
        )

    @app.get("/admin/export-metrics")
    def admin_export_metrics() -> Response:
        if not _admin_ok():
            return Response("forbidden", status=403)
        db_path = app.config["NUDGE_DB"].path
        bundle = export_metrics_zip(db_path=db_path, anon_salt=cfg.anon_salt)
        resp = Response(bundle.data, mimetype=bundle.content_type)
        resp.headers["Content-Disposition"] = f'attachment; filename="{bundle.filename}"'
        return resp

    @app.get("/admin/rl-version")
    def admin_get_rl_version() -> Response:
        if not _admin_ok():
            return Response("forbidden", status=403)
        db_path = app.config["NUDGE_DB"].path
        conn = connect(db_path)
        try:
            row = conn.execute("SELECT value FROM system_kv WHERE key = 'rl_active_version'").fetchone()
            active = str(row["value"]) if row is not None else None
        finally:
            conn.close()

        available: list[str] = []
        if cfg.rl_model_dir:
            from pathlib import Path

            base = Path(str(cfg.rl_model_dir))
            if base.exists():
                for p in base.iterdir():
                    if not p.is_dir():
                        continue
                    if (p / "model.zip").exists():
                        available.append(p.name)
        available.sort()
        return jsonify({"active": active, "available": available})

    @app.post("/admin/rl-version")
    def admin_set_rl_version() -> Response:
        if not _admin_ok():
            return Response("forbidden", status=403)
        version = request.args.get("version")
        if not version:
            payload = request.get_json(silent=True) or {}
            version = str(payload.get("version") or "").strip() or None
        if not version:
            return Response("missing version", status=400)
        db_path = app.config["NUDGE_DB"].path
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO system_kv(key, value, updated_at)
                VALUES ('rl_active_version', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(version),),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return jsonify({"active": str(version)})

    return app
