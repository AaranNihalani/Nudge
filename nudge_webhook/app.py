from __future__ import annotations

import os

import json

from flask import Flask, Response, jsonify, render_template, request
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
                "claude_model": cfg.claude_model,
                "claude_key_present": bool(cfg.claude_api_key),
                "verbose_replies": bool(getattr(cfg, "verbose_replies", False)),
                "debug_claude": bool(getattr(cfg, "debug_claude", False)),
                "render_git_commit": os.environ.get("RENDER_GIT_COMMIT"),
            }
        )

    @app.get("/")
    def web_chat() -> Response:
        return Response(render_template("index.html"), mimetype="text/html")

    def _parse_status_line(reply: str) -> dict[str, str]:
        return {}

    @app.post("/api/chat")
    def api_chat() -> Response:
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id") or "").strip()
        message = str(payload.get("message") or "").strip()
        if session_id == "" or message == "":
            return Response("missing session_id or message", status=400)

        inbound = InboundMessage(
            from_addr=f"web:{session_id}",
            to_addr="web",
            body=message,
            twilio_message_sid=None,
            payload={"source": "web"},
        )
        db_path = app.config["NUDGE_DB"].path
        reply_text = process_twilio_inbound(cfg, db_path=db_path, inbound=inbound)

        conn = connect(db_path)
        try:
            row = conn.execute(
                "SELECT id, consent_status, district FROM users WHERE phone_e164 = ?",
                (f"web:{session_id}",),
            ).fetchone()
            user_id = int(row["id"]) if row is not None else None
            consent_status = str(row["consent_status"]) if row is not None else None
            district = str(row["district"]) if row is not None and row["district"] is not None else None
            mfi_districts = int(conn.execute("SELECT COUNT(*) AS c FROM mfi_districts").fetchone()["c"])

            last_event = None
            draft_payload = None
            draft_model = None
            if user_id is not None:
                last_event = conn.execute(
                    """
                    SELECT intent, confidence, amount_inr, tenure_days, interest_rate_apr, lender_type, negotiation_stage, model, parsed_at
                    FROM parsed_events
                    WHERE user_id = ? AND event_type = 'borrow_intent'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (int(user_id),),
                ).fetchone()
                session_row = conn.execute(
                    """
                    SELECT borrow_draft_json, borrow_model
                    FROM user_sessions
                    WHERE user_id = ?
                    """,
                    (int(user_id),),
                ).fetchone()
                if session_row is not None and session_row["borrow_draft_json"]:
                    try:
                        maybe_draft = json.loads(str(session_row["borrow_draft_json"]))
                        if isinstance(maybe_draft, dict):
                            draft_payload = maybe_draft
                            draft_model = str(session_row["borrow_model"]) if session_row["borrow_model"] is not None else None
                    except Exception:
                        draft_payload = None
        finally:
            conn.close()

        status = _parse_status_line(reply_text)

        debug: dict[str, object] = {
            "policy": status.get("policy"),
            "engine": status.get("engine"),
            "decision": status.get("decision"),
            "parsed": status.get("parsed"),
            "intent": status.get("intent"),
            "confidence": status.get("confidence"),
            "limits": status.get("limits"),
            "district": district,
            "consent_status": consent_status,
            "mfi_districts": mfi_districts,
            "claude_enabled": bool(cfg.claude_api_key),
            "claude_model": cfg.claude_model,
        }

        if draft_payload is not None:
            debug["last_borrow_intent"] = {
                "intent": bool(draft_payload["intent"]) if draft_payload.get("intent") is not None else None,
                "confidence": float(draft_payload["confidence"]) if draft_payload.get("confidence") is not None else None,
                "amount_inr": float(draft_payload["amount_inr"]) if draft_payload.get("amount_inr") is not None else None,
                "tenure_days": int(draft_payload["tenure_days"]) if draft_payload.get("tenure_days") is not None else None,
                "interest_rate_apr": float(draft_payload["interest_rate_apr"]) if draft_payload.get("interest_rate_apr") is not None else None,
                "lender_type": str(draft_payload["lender_type"]) if draft_payload.get("lender_type") is not None else None,
                "negotiation_stage": str(draft_payload["negotiation_stage"]) if draft_payload.get("negotiation_stage") is not None else None,
                "model": draft_model or "draft",
                "parsed_at": None,
                "source": "draft",
            }
        elif last_event is not None:
            debug["last_borrow_intent"] = {
                "intent": bool(int(last_event["intent"])) if last_event["intent"] is not None else None,
                "confidence": float(last_event["confidence"]) if last_event["confidence"] is not None else None,
                "amount_inr": float(last_event["amount_inr"]) if last_event["amount_inr"] is not None else None,
                "tenure_days": int(last_event["tenure_days"]) if last_event["tenure_days"] is not None else None,
                "interest_rate_apr": float(last_event["interest_rate_apr"]) if last_event["interest_rate_apr"] is not None else None,
                "lender_type": str(last_event["lender_type"]) if last_event["lender_type"] is not None else None,
                "negotiation_stage": str(last_event["negotiation_stage"]) if last_event["negotiation_stage"] is not None else None,
                "model": str(last_event["model"]) if last_event["model"] is not None else None,
                "parsed_at": str(last_event["parsed_at"]) if last_event["parsed_at"] is not None else None,
                "source": "parsed",
            }

        return jsonify({"reply": reply_text, "debug": debug})

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
