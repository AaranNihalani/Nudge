from __future__ import annotations

from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class TwilioSendResult:
    sid: str
    status: str | None


def _apply_channel(addr: str, *, channel: str) -> str:
    a = (addr or "").strip()
    if channel == "whatsapp":
        if not a.lower().startswith("whatsapp:"):
            return f"whatsapp:{a}"
    return a


def send_message(cfg: Config, *, to_phone_e164: str, body: str) -> TwilioSendResult:
    if not cfg.twilio_account_sid or not cfg.twilio_auth_token:
        raise RuntimeError("twilio_not_configured")
    if not cfg.twilio_from_addr:
        raise RuntimeError("twilio_from_not_configured")

    from twilio.rest import Client

    channel = str(cfg.default_channel or "whatsapp").strip().lower()
    client = Client(cfg.twilio_account_sid, cfg.twilio_auth_token)
    msg = client.messages.create(
        to=_apply_channel(to_phone_e164, channel=channel),
        from_=_apply_channel(cfg.twilio_from_addr, channel=channel),
        body=str(body or "").strip(),
    )
    return TwilioSendResult(sid=str(getattr(msg, "sid", "")), status=str(getattr(msg, "status", "")) or None)

