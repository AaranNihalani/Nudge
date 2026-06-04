from __future__ import annotations

import json
import os

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .bot import InboundMessage, process_inbound
from .config import Config
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

    # ── Public routes ──────────────────────────────────────────────────────

    @app.get("/")
    def landing() -> Response:
        return Response(render_template("landing.html"), mimetype="text/html")

    @app.get("/chat")
    def web_chat() -> Response:
        return Response(render_template("index.html"), mimetype="text/html")

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
            pass
        return jsonify({
            "status": "ok",
            "db_path": getattr(db_info, "path", None),
            "db_schema_version": getattr(db_info, "schema_version", None),
            "mfi_districts": mfi_districts,
            "claude_model": cfg.claude_model,
            "claude_key_present": bool(cfg.claude_api_key),
        })

    # ── Chat API ───────────────────────────────────────────────────────────

    @app.post("/api/chat")
    def api_chat() -> Response:
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not session_id or not message:
            return Response("missing session_id or message", status=400)

        inbound = InboundMessage(
            from_addr=f"web:{session_id}",
            to_addr="web",
            body=message,
            message_sid=None,
            payload={"source": "web"},
        )
        db_path = app.config["NUDGE_DB"].path
        reply_text = process_inbound(cfg, db_path=db_path, inbound=inbound)

        conn = connect(db_path)
        try:
            row = conn.execute(
                "SELECT id, consent_status, district FROM users WHERE phone_e164 = ?",
                (f"web:{session_id}",),
            ).fetchone()
            user_id = int(row["id"]) if row else None
            consent_status = str(row["consent_status"]) if row else None
            district = str(row["district"]) if row and row["district"] else None
            mfi_count = int(conn.execute("SELECT COUNT(*) AS c FROM mfi_districts").fetchone()["c"])

            last_event = None
            draft_payload = None
            draft_model = None
            if user_id is not None:
                last_event = conn.execute(
                    """
                    SELECT intent, confidence, amount_inr, tenure_days, interest_rate_apr,
                           lender_type, negotiation_stage, model, parsed_at
                    FROM parsed_events
                    WHERE user_id = ? AND event_type = 'borrow_intent'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(user_id),),
                ).fetchone()
                session_row = conn.execute(
                    "SELECT borrow_draft_json, borrow_model FROM user_sessions WHERE user_id = ?",
                    (int(user_id),),
                ).fetchone()
                if session_row and session_row["borrow_draft_json"]:
                    try:
                        draft_payload = json.loads(str(session_row["borrow_draft_json"]))
                        draft_model = str(session_row["borrow_model"]) if session_row["borrow_model"] else None
                    except Exception:
                        pass
        finally:
            conn.close()

        debug: dict = {
            "district": district,
            "consent_status": consent_status,
            "mfi_districts": mfi_count,
            "claude_enabled": bool(cfg.claude_api_key),
        }
        if draft_payload and isinstance(draft_payload, dict):
            debug["last_borrow_intent"] = {**draft_payload, "source": "draft", "model": draft_model or "draft"}
        elif last_event:
            debug["last_borrow_intent"] = {
                "intent": bool(int(last_event["intent"])) if last_event["intent"] is not None else None,
                "confidence": float(last_event["confidence"]) if last_event["confidence"] else None,
                "amount_inr": float(last_event["amount_inr"]) if last_event["amount_inr"] else None,
                "tenure_days": int(last_event["tenure_days"]) if last_event["tenure_days"] else None,
                "interest_rate_apr": float(last_event["interest_rate_apr"]) if last_event["interest_rate_apr"] else None,
                "lender_type": str(last_event["lender_type"]) if last_event["lender_type"] else None,
                "negotiation_stage": str(last_event["negotiation_stage"]) if last_event["negotiation_stage"] else None,
                "model": str(last_event["model"]) if last_event["model"] else None,
                "source": "parsed",
            }

        return jsonify({"reply": reply_text, "debug": debug})

    # ── Admin endpoints ────────────────────────────────────────────────────

    @app.get("/admin/export-metrics")
    def admin_export_metrics() -> Response:
        if not _admin_ok():
            return Response("forbidden", status=403)
        db_path = app.config["NUDGE_DB"].path
        bundle = export_metrics_zip(db_path=db_path, anon_salt=cfg.anon_salt)
        resp = Response(bundle.data, mimetype=bundle.content_type)
        resp.headers["Content-Disposition"] = f'attachment; filename="{bundle.filename}"'
        return resp

    return app
