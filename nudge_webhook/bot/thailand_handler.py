"""Thailand-specific message handler for /api/thailand/chat."""
from __future__ import annotations

import difflib
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

from ..config import Config
from ..db import connect, init_and_migrate
from ..mfi import load_dataset_into_sqlite
from ..thailand_data import (
    PROVINCE_ALIASES,
    THAI_CHAT_CONTEXT,
    THAI_PROVINCES,
    FIELDWORK_PROVINCE,
    FIELDWORK_DISTRICT,
)
from .helpers import now_utc
from .loan import InboundMessage


# ---------------------------------------------------------------------------
# Thailand DB — separate SQLite populated from thailand_lender_tiers.csv
# ---------------------------------------------------------------------------

_TH_DB_INFO: Any = None


def _get_thailand_db_path() -> str:
    return os.path.join(os.getcwd(), "data", "thailand_nudge.sqlite3")


def init_thailand_db() -> str:
    """Initialise the Thailand SQLite DB and load lender tiers if not already done."""
    global _TH_DB_INFO
    db_path = _get_thailand_db_path()
    _TH_DB_INFO = init_and_migrate(db_path)

    # Load lender tiers if missing or stale (< 70 provinces = old 15-province CSV)
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(DISTINCT name) AS n FROM mfi_districts").fetchone()
        n_provinces = int(row["n"]) if row else 0
    finally:
        conn.close()

    if n_provinces < 70:
        dataset_path = os.path.join(os.getcwd(), "datasets", "thailand_lender_tiers.csv")
        if os.path.exists(dataset_path):
            try:
                load_dataset_into_sqlite(db_path, dataset_path, replace=True)
                print(f"[Thailand] Loaded lender tiers ({n_provinces} provinces → now 77)")
            except Exception as e:
                print(f"[Thailand] Warning: could not load lender tiers: {e}")

    return db_path


# ---------------------------------------------------------------------------
# Province normalisation (mirrors India district normalisation)
# ---------------------------------------------------------------------------

def _norm_province(s: str) -> str:
    raw = (s or "").strip().lower()
    for ch in [".", ",", "-", "/"]:
        raw = raw.replace(ch, " ")
    return " ".join(raw.split())


def _canonical_province(candidate: str) -> str | None:
    cand_norm = _norm_province(candidate)
    if not cand_norm:
        return None

    # Check alias table first
    alias = PROVINCE_ALIASES.get(cand_norm)
    if alias:
        return alias

    # Exact match against province list (case-insensitive)
    for p in THAI_PROVINCES:
        if _norm_province(p) == cand_norm:
            return p

    # Prefix / contains match
    near = [p for p in THAI_PROVINCES if _norm_province(p).startswith(cand_norm) or cand_norm.startswith(_norm_province(p))]
    if len(near) == 1:
        return near[0]

    return None


def _suggest_provinces(candidate: str, *, limit: int = 3) -> list[str]:
    cand_norm = _norm_province(candidate)
    by_norm = {_norm_province(p): p for p in THAI_PROVINCES}
    matches = difflib.get_close_matches(cand_norm, list(by_norm.keys()), n=limit, cutoff=0.68)
    return [by_norm[m] for m in matches if m in by_norm]


def _looks_like_province_name(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or any(ch.isdigit() for ch in raw) or len(raw) > 60:
        return False
    low = raw.lower()
    blocked = {
        "help", "stop", "start", "yes", "no", "skip", "more", "hello", "hi",
        "hey", "thanks", "thank you", "show provinces", "browse provinces",
    }
    return low not in blocked and any(ch.isalpha() for ch in raw)


# ---------------------------------------------------------------------------
# Thai currency formatting
# ---------------------------------------------------------------------------

def _fmt_thb(amount: float) -> str:
    return f"฿{int(round(amount)):,}"


def _simple_interest(amount: float, tenure_days: int, apr_pct: float) -> tuple[float, float]:
    ratio = (apr_pct / 100.0) * (tenure_days / 365.0)
    interest = max(0.0, amount * ratio)
    return interest, amount + interest


def _format_percent(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Lender lookup (Thailand DB)
# ---------------------------------------------------------------------------

def _has_province_rates(conn, *, province: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM mfi_rates r JOIN mfi_districts d ON d.id = r.district_id WHERE lower(d.name) = lower(?) LIMIT 1",
        (province,),
    ).fetchone()
    return row is not None


def _provinces_in_db(conn) -> list[str]:
    return [str(r["name"]) for r in conn.execute("SELECT name FROM mfi_districts ORDER BY name ASC").fetchall()]


def _canonical_province_db(conn, candidate: str) -> str | None:
    cand_norm = _norm_province(candidate)
    for name in _provinces_in_db(conn):
        if _norm_province(name) == cand_norm:
            return name
    return _canonical_province(candidate)


def _recommend_tiers(conn, *, province: str, current_rate_apr: float | None = None, n: int = 3) -> list[dict]:
    params: list[Any] = [province]
    extra = ""
    if current_rate_apr is not None:
        extra = " AND r.rate_apr < ?"
        params.append(float(current_rate_apr))
    params.append(n)
    rows = conn.execute(
        f"""
        SELECT d.name AS province, l.name AS lender, r.rate_apr, r.effective_date, r.source
        FROM mfi_rates r
        JOIN mfi_districts d ON d.id = r.district_id
        JOIN mfi_lenders l ON l.id = r.lender_id
        WHERE d.name = ?{extra}
        ORDER BY r.rate_apr ASC, l.name COLLATE NOCASE ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    if not rows:
        # Fallback: show all options regardless of current rate
        rows = conn.execute(
            """
            SELECT d.name AS province, l.name AS lender, r.rate_apr, r.effective_date, r.source
            FROM mfi_rates r
            JOIN mfi_districts d ON d.id = r.district_id
            JOIN mfi_lenders l ON l.id = r.lender_id
            WHERE d.name = ?
            ORDER BY r.rate_apr ASC
            LIMIT ?
            """,
            (province, n),
        ).fetchall()
    return [dict(r) for r in rows]


def _render_tier_list(rows: list[dict], *, amount_thb: float | None, tenure_days: int | None) -> str:
    parts = []
    for i, r in enumerate(rows, 1):
        lender = str(r.get("lender") or "")
        rate = float(r.get("rate_apr") or 0.0)
        monthly = rate / 12.0
        line = f"{i}. **{lender}** — {rate:g}% APR (~{_format_percent(monthly)}%/month)"
        if amount_thb and tenure_days:
            interest, total = _simple_interest(amount_thb, tenure_days, rate)
            months = max(1, math.ceil(tenure_days / 30))
            monthly_pay = total / months
            line += f"\n   {_fmt_thb(total)} total over {months} month{'s' if months != 1 else ''} ({_fmt_thb(monthly_pay)}/month)"
        parts.append(line)
    return "\n".join(parts)


_LENDER_NOTES: dict[str, str] = {
    # ── Government / subsidised ───────────────────────────────────────────────
    "village fund": "community revolving fund — ~79,610 funds nationally; for registered members; villagefund.or.th",
    "baac half-half": "government-subsidised; registered farmers only; valid to April 2029",
    "baac standard": "state agricultural bank; farmers and rural borrowers; baac.or.th",
    "gsb welfare card": "state welfare card holders only; no guarantor needed; gsb.or.th",
    "gsb people": "government savings bank; limit ฿200,000; needs guarantor or property; rate estimated — verify at gsb.or.th",
    # ── GSB subsidiary — Good Money ───────────────────────────────────────────
    "good money by gsb: nano": (
        "GSB subsidiary; BOT Nano Finance; targets informal workers and low-income earners; "
        "no collateral; 30-min app approval; gsb.or.th/personal/good-money"
    ),
    "good money by gsb: personal": (
        "GSB subsidiary; personal loan up to ฿1,000,000; targets workers without standard income docs; "
        "gsb.or.th/personal/good-money"
    ),
    "good money": "GSB subsidiary (เงินดีดี); targets low-income informal workers; app-based; gsb.or.th/personal/good-money",
    # ── Commercial bank products with relaxed income requirements ─────────────
    "krungthai": "requires min. ฿30,000/month income — excludes most informal-sector borrowers",
    "krungsri first choice payplus": (
        "NO income documents required; digital alternative data scoring; "
        "limit ฿20,000; up to 5-month repayment; targets self-employed and freelancers"
    ),
    "scb up": (
        "SCB revolving credit for people WITHOUT fixed income or proof of income; "
        "min ฿1,000 credit line; builds credit history for first-time borrowers; scb.co.th"
    ),
    # ── Thai Credit Bank — only commercial bank focused on nano/micro ─────────
    "thai credit bank: nano": (
        "Thailand's only commercial bank focused on nano lending; "
        "no collateral; no guarantor; up to ฿30,000; 6–12 months; 267 branches; thaicreditbank.com"
    ),
    "thai credit bank: micro": (
        "Thai Credit Bank micro credit; no collateral; no guarantor; "
        "up to ฿200,000; targets micro-SME, freelancers, online sellers; thaicreditbank.com"
    ),
    "thai credit bank": "Thailand's only commercial bank focused on nano/micro lending; no collateral; 267 branches; thaicreditbank.com",
    # ── Vehicle title (accessible to freelancers if they own a vehicle) ────────
    "tidlor": (
        "vehicle title loan — must own a car, motorcycle, or truck; "
        "accepts employees, self-employed, AND freelancers; 12–24% APR; tidlor.com/en/loan/car"
    ),
    # ── Nano Finance operators ────────────────────────────────────────────────
    "siam digital": "BOT Nano Finance; digital application at siamdl.co.th; min income ฿12,000/month; no collateral",
    "muangthai capital": "SET-listed BOT Nano Finance (MTC.BK); nationwide branches; no collateral; limit ฿100,000",
    "srisawad": "SET-listed BOT Nano Finance (SAWAD.BK); large nationwide branch network; no collateral; limit ฿100,000",
    "saksiam leasing": "BOT Nano Finance; 27.96–33% APR; nationwide branches (HQ Uttaradit); own business required; saksiam.com",
    "aira & aiful": (
        "BOT Nano Finance; 45 branches nationwide; requires guarantor (income ≥฿9,000/month); "
        "age 20–60; own business; amoney.co.th"
    ),
    "rabbit cash": (
        "digital BOT Nano Finance (BTS + AEON JV); 100% app-based; "
        "specifically targets freelancers and online sellers without standard income docs"
    ),
    "maccabee group": "BOT Nano Finance (2015 original licensee); Bangkok + Khon Kaen + Roi Et",
    "sahapaibuul": "BOT Nano Finance (2015 original licensee); Northeast provinces (Roi Et, Maha Sarakham, Kalasin, Khon Kaen)",
    "thai ace capital": "BOT Nano Finance; ~15% interest + 5% fee (~20% APR all-in); Bangkok + Nakhon Ratchasima; tcapital.co.th",
    "m capital": "BOT Nano Finance; 2.75%/month; requires guarantor; Bangkok metro; targets traders and merchants",
    "nano finance": "BOT Nano Finance cap (33% APR all-in); ~55–70 licensed operators; find providers at app.bot.or.th/botlicensecheck",
    # ── Pico Finance operators ────────────────────────────────────────────────
    "pueantae quick money": (
        "NAMED Pico Finance operator (เพื่อนแท้ ควิกมันนี่); licence ว00007/2565; "
        "rate from 0.60%/month (7.2% APR) — far below the 36% cap; "
        "branches: Bangkok (Don Mueang, Min Buri), Pathum Thani (Rangsit), Chon Buri, Rayong; "
        "phone 02-114-8988; puean.co.th"
    ),
    "pico plus": "FPO-licensed; limit ฿100,000 (vs ฿50k standard Pico); find operator in your province at fpo.go.th",
    "pico finance": (
        "FPO-licensed (Fiscal Policy Office, Ministry of Finance); limit ฿50,000; max 36% APR all-in; "
        "1,155 operators in 75 provinces (May 2025); province-restricted lending; "
        "search your province at 1359.go.th/picodoc/pico_public"
    ),
}


def _lender_note(name: str) -> str:
    low = name.lower()
    for key, note in _LENDER_NOTES.items():
        if key in low:
            return f" *({note})*"
    return ""


def _render_tier_list(rows: list[dict], *, amount_thb: float | None, tenure_days: int | None) -> str:
    parts = []
    for i, r in enumerate(rows, 1):
        lender = str(r.get("lender") or "")
        rate = float(r.get("rate_apr") or 0.0)
        monthly = rate / 12.0
        line = f"{i}. **{lender}** — {rate:g}% APR (~{_format_percent(monthly)}%/month){_lender_note(lender)}"
        if amount_thb and tenure_days:
            interest, total = _simple_interest(amount_thb, tenure_days, rate)
            months = max(1, math.ceil(tenure_days / 30))
            monthly_pay = total / months
            line += f"\n   Repay **{_fmt_thb(total)}** total over {months} month{'s' if months != 1 else ''} ({_fmt_thb(monthly_pay)}/month)"
        parts.append(line)
    return "\n\n".join(parts)


def _suggest_tier_message(conn, *, province: str, current_rate_apr: float | None = None,
                          amount_thb: float | None = None, tenure_days: int | None = None) -> str:
    rows = _recommend_tiers(conn, province=province, current_rate_apr=current_rate_apr)
    if not rows:
        return (
            f"No lender data found for {province}. Nationwide regulated options:\n\n"
            "- **Pico Finance** — max 36% APR, ฿50k limit. Find local operators at fpo.go.th\n"
            "- **Nano Finance** — max 33% APR, ฿100k limit. Find providers at app.bot.or.th/botlicensecheck\n"
            "- **Village Fund** — ~6% APR, ฿10–20k. Find yours at villagefund.or.th\n"
            "- **BAAC** — from 3% APR for registered farmers. baac.or.th"
        )
    joined = _render_tier_list(rows, amount_thb=amount_thb, tenure_days=tenure_days)
    sel = "Send **1** for more detail." if len(rows) == 1 else "Send **1**, **2**, or **3** to see eligibility and next steps."
    ask = "" if (amount_thb and tenure_days) else "\n\nSend the loan amount and time period to see the ฿ totals."
    return (
        f"Regulated options in **{province}** (cheapest first):\n\n{joined}\n\n{sel}{ask}\n\n"
        "*Figures assume simple interest — actual fees may vary. Rates current as of data date.*"
    )


def _alert_tier_message(conn, *, province: str, quoted_apr: float,
                        amount_thb: float | None = None, tenure_days: int | None = None) -> str:
    rows = _recommend_tiers(conn, province=province, current_rate_apr=quoted_apr)
    monthly_quoted = _format_percent(quoted_apr / 12.0)

    if not rows:
        return (
            f"At {quoted_apr:g}% APR (~{monthly_quoted}%/month), that rate is very high.\n\n"
            f"Regulated options to compare in {province}:\n\n"
            "**Village Fund (กองทุนหมู่บ้าน)** — ~6% APR. Community revolving funds; ~79,610 funds nationwide. "
            "Loans typically ฿10,000–20,000 for registered community members. Find yours at villagefund.or.th\n\n"
            "**BAAC (ธ.ก.ส.)** — 6.625% APR (MRR). State agricultural bank. Farmers with a BAAC account "
            "may qualify for subsidised rates as low as 3% under the Half-Half programme. baac.or.th\n\n"
            "**Pico Finance (พิโกไฟแนนซ์)** — max 36% APR, limit ฿50,000. Licensed by the Fiscal Policy Office (FPO). "
            "1,155 operators in 75 provinces. To find a licensed operator near you, visit fpo.go.th or search "
            "\"พิโกไฟแนนซ์\" + your province name.\n\n"
            "**Nano Finance (นาโนไฟแนนซ์)** — max 33% APR, limit ฿100,000. Licensed by the Bank of Thailand. "
            "Named operators: Muangthai Capital (MTC), Srisawad Corporation (SAWAD), Siam Digital Lending (siamdl.co.th). "
            "Find all licensed providers at app.bot.or.th/botlicensecheck\n\n"
            "All regulated lenders must keep effective rates (interest + fees + insurance) within their statutory cap."
        )

    joined = _render_tier_list(rows, amount_thb=amount_thb, tenure_days=tenure_days)
    savings_line = ""
    if amount_thb and tenure_days:
        _, quoted_total = _simple_interest(amount_thb, tenure_days, quoted_apr)
        best_rate = float(min(float(r.get("rate_apr") or 0) for r in rows))
        _, best_total = _simple_interest(amount_thb, tenure_days, best_rate)
        save = max(0.0, quoted_total - best_total)
        if save > 0:
            savings_line = (
                f"\nAt your quoted rate you'd repay {_fmt_thb(quoted_total)} total. "
                f"The cheapest option above is {_fmt_thb(best_total)} — about {_fmt_thb(save)} less.\n"
            )

    return (
        f"At {quoted_apr:g}% APR (~{monthly_quoted}%/month), that's costly. "
        f"Regulated alternatives in {province}:\n\n{joined}\n{savings_line}\n"
        "Send 1, 2, or 3 to see more details.\n\n"
        "Numbers assume simple interest and no extra fees."
    )


# ---------------------------------------------------------------------------
# Language-aware template strings (used when Claude is unavailable)
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "help": (
            "I can help you understand how expensive a loan offer is, estimate what "
            "you'd repay in ฿, and show regulated alternatives in your province.\n\n"
            "To get started:\n"
            "1) Tell me your **province** (e.g. Bangkok, Chiang Mai, Khon Kaen).\n"
            "2) Tell me the loan: amount + time + rate (APR or %/month) if you have it.\n\n"
            "Examples:\n"
            "- \"I'm in Bangkok\"\n"
            "- \"Need ฿5,000 for 30 days at 10% monthly\"\n"
            "- \"Show provinces\""
        ),
        "show_provinces": "All 77 Thai provinces are supported.\nExamples: {sample} … and more.\n\nJust type your province name.",
        "ask_province": "What province are you in?\n{sug}Examples: Bangkok, Chiang Mai, Khon Kaen\nType *show provinces* to browse all 77.",
        "loan_no_province": "I can help with that. Which province are you in?\n{sug}Examples: Bangkok, Chiang Mai, Khon Kaen",
        "confirm_province": "Province confirmed: **{province}**.{fieldwork}\n\nNow tell me the loan offer — amount, time period, and rate if you have it.\n\nExample: \"Need ฿5,000 for 30 days at 10% monthly.\"",
        "prompt_loan": "Province: **{province}**. Tell me the loan you're considering:\n\n- Amount in ฿\n- How long (days or months)\n- Rate (APR or % per month) if you have it\n\nExample: \"Need ฿10,000 for 60 days at 8% monthly.\"\n\nOr say *help* for more options.",
        "need_tenure": "Got the amount ({amount}). How long is the loan — days or months?",
        "need_amount": "Got the tenure ({tenure} days). What's the loan amount in ฿?",
    },
    "th": {
        "help": (
            "สวัสดีครับ — ผมช่วยประเมินต้นทุนเงินกู้ในหน่วย ฿ และเปรียบเทียบกับทางเลือกที่มีการกำกับดูแล เช่น "
            "พิโกไฟแนนซ์ นาโนไฟแนนซ์ และกองทุนหมู่บ้านได้\n\n"
            "เริ่มต้นได้ดังนี้:\n"
            "1) บอก**จังหวัด**ของคุณ (เช่น กรุงเทพฯ เชียงใหม่ ขอนแก่น)\n"
            "2) บอกรายละเอียดเงินกู้: จำนวนเงิน + ระยะเวลา + อัตราดอกเบี้ย (ถ้าทราบ)\n\n"
            "ตัวอย่าง:\n"
            "- \"ฉันอยู่กรุงเทพฯ\"\n"
            "- \"กู้ ฿5,000 30 วัน ดอก 10% ต่อเดือน\"\n"
            "- \"แสดงจังหวัด\""
        ),
        "show_provinces": "รองรับทุก 77 จังหวัดทั่วไทย\nตัวอย่าง: {sample} … และอื่นๆ\n\nพิมพ์ชื่อจังหวัดของคุณได้เลยครับ",
        "ask_province": "คุณอยู่จังหวัดไหนครับ?\n{sug}ตัวอย่าง: กรุงเทพฯ เชียงใหม่ ขอนแก่น\nพิมพ์ *แสดงจังหวัด* เพื่อดูทั้ง 77 จังหวัด",
        "loan_no_province": "ช่วยได้ครับ — คุณอยู่จังหวัดไหน?\n{sug}ตัวอย่าง: กรุงเทพฯ เชียงใหม่ ขอนแก่น",
        "confirm_province": "รับทราบ — จังหวัด: **{province}**{fieldwork}\n\nบอกข้อมูลเงินกู้ได้เลยครับ (จำนวนเงิน + ระยะเวลา + อัตราดอกเบี้ย ถ้าทราบ)\n\nตัวอย่าง: \"กู้ ฿5,000 30 วัน ดอก 10% ต่อเดือน\"",
        "prompt_loan": "จังหวัด: **{province}** — บอกรายละเอียดเงินกู้:\n\n- จำนวนเงิน ฿\n- ระยะเวลา (วันหรือเดือน)\n- อัตราดอกเบี้ย (APR หรือ %/เดือน) ถ้าทราบ\n\nตัวอย่าง: \"กู้ ฿10,000 60 วัน ดอก 8%/เดือน\"\n\nพิมพ์ *ช่วยเหลือ* สำหรับข้อมูลเพิ่มเติม",
        "need_tenure": "ได้รับจำนวนเงินแล้ว ({amount}) — กู้นานแค่ไหนครับ? (วันหรือเดือน)",
        "need_amount": "ได้รับระยะเวลาแล้ว ({tenure} วัน) — จำนวนเงินกู้ ฿ เท่าไหร่ครับ?",
    },
}

_LANG_INSTRUCTION = {
    "en": "Respond in clear, simple English.",
    "th": "ตอบเป็นภาษาไทยที่เป็นธรรมชาติและเข้าใจง่าย — ใช้ภาษาไทยเท่านั้น ห้ามตอบเป็นภาษาอังกฤษ",
}


def _s(key: str, lang: str, **kwargs: str) -> str:
    """Return the template string for key/lang, formatted with kwargs."""
    text = _STRINGS.get(lang, _STRINGS["en"]).get(key, _STRINGS["en"][key])
    return text.format(**kwargs) if kwargs else text


def _system_for_lang(lang: str) -> str:
    """Return the Claude system prompt with the correct language instruction."""
    instruction = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
    return f"{THAI_CHAT_CONTEXT}\n\n{instruction}"


# ---------------------------------------------------------------------------
# Claude humanisation for Thai context
# ---------------------------------------------------------------------------

def _humanize_thai(cfg: Config, *, fallback: str, purpose: str, lang: str = "en") -> str | None:
    from ..claude import generate_reply
    prompt = (
        "Rewrite the message below as a natural chatbot reply. "
        "Preserve every Baht amount (฿), percentage, lender name, province name, numbered list item, and command exactly. "
        "Do not add facts, phone numbers, legal advice, or new commands. "
        "Do not start with a greeting word (Hey, Hi, Hello, Sure, Great, etc.). "
        "Use markdown: **bold** for lender names and key ฿ figures. "
        f"Keep it concise and easy to act on.\n\nPurpose: {purpose}\nMessage:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt, system=_system_for_lang(lang))
    return reply.strip() or None if reply else None


def _recommendation_thai(cfg: Config, *, fallback: str, province: str,
                         rows: list[dict], amount_thb: float | None,
                         tenure_days: int | None, current_rate: float | None,
                         lang: str = "en") -> str | None:
    if not rows:
        return None
    from ..claude import generate_reply
    prompt = (
        "Rewrite this message as a natural chatbot response. "
        "Preserve every numbered lender option, lender name, APR, monthly rate, ฿ amount, "
        "repayment amount, time period, and command exactly. "
        "Do not add approval claims, phone numbers, or extra lenders. "
        "Do not start with a greeting. Use **bold** for key figures. "
        "Keep it concise and easy to act on.\n\n"
        f"Province: {province}\nLoan amount ฿: {amount_thb}\nTenure days: {tenure_days}\n"
        f"Quoted APR: {current_rate}\nFacts:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt, system=_system_for_lang(lang))
    return reply.strip() or None if reply else None


# ---------------------------------------------------------------------------
# Loan parsing (reuse India parsers — amounts and rates are language-agnostic)
# ---------------------------------------------------------------------------

def _parse_amount(text: str) -> float | None:
    from .parsers import parse_amount_inr
    return parse_amount_inr(text)


def _parse_tenure(text: str) -> int | None:
    from .parsers import parse_tenure_days
    return parse_tenure_days(text)


def _parse_rate(text: str) -> float | None:
    from .parsers import parse_interest_rate_apr
    return parse_interest_rate_apr(text)


# ---------------------------------------------------------------------------
# Session helpers (reuse India session module against the Thailand DB)
# ---------------------------------------------------------------------------

def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def process_thailand_inbound(cfg: Config, *, db_path: str, inbound: InboundMessage, lang: str = "en") -> str:
    """
    Entry point for /api/thailand/chat.
    Uses the same DB as India for users/sessions (province stored in 'district' column),
    but a separate Thailand DB (db_path) for the lender/tier lookup.
    Session IDs are prefixed 'thailand:' to isolate Thai sessions from Indian ones.
    """
    now_dt = now_utc()
    from_addr = f"thailand:{inbound.from_addr}"
    text = inbound.body.strip()

    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")

        # Get or create user
        row = conn.execute(
            "SELECT id, district FROM users WHERE phone_e164 = ?", (from_addr,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users(phone_e164, consent_status) VALUES (?, 'opted_in')", (from_addr,)
            )
            row = conn.execute(
                "SELECT id, district FROM users WHERE phone_e164 = ?", (from_addr,)
            ).fetchone()

        user_id = int(row["id"])
        province = str(row["district"]) if row["district"] else None

        # Log inbound
        conn.execute(
            "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json) "
            "VALUES (?, 'inbound', 'web', ?, ?, ?, ?)",
            (user_id, inbound.from_addr, inbound.to_addr, text, json.dumps({"source": "thailand_web"})),
        )

        reply = _route_message(cfg, conn=conn, db_path=db_path,
                               user_id=user_id, province=province, text=text, now_dt=now_dt, lang=lang)

        # Log outbound
        conn.execute(
            "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json) "
            "VALUES (?, 'outbound', 'web', ?, ?, ?, ?)",
            (user_id, inbound.to_addr or "", inbound.from_addr, reply,
             json.dumps({"generated_at": _fmt_ts(now_dt)})),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return reply


def _route_message(cfg: Config, *, conn, db_path: str, user_id: int,
                   province: str | None, text: str, now_dt: datetime, lang: str = "en") -> str:
    low = text.strip().lower()

    # Help
    if low in {"help", "how does this work", "what can you do", "?", "ช่วยเหลือ", "วิธีใช้"}:
        fallback = _s("help", lang)
        return _humanize_thai(cfg, fallback=fallback, purpose="help user understand what the chatbot can do", lang=lang) or fallback

    # Show provinces list
    if re.search(r"\b(show|browse|list)\b.*\bprovince", low) or low in {"show provinces", "provinces", "แสดงจังหวัด", "จังหวัด"}:
        sample = ", ".join(THAI_PROVINCES[:20])
        return _s("show_provinces", lang, sample=sample)

    # Province setting — explicit "I'm in X" or "set province X"
    province_cmd = _extract_province_command(text)
    if province_cmd is not None:
        canonical = _canonical_province(province_cmd)
        chosen = canonical or province_cmd.strip()
        conn.execute("UPDATE users SET district = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chosen, user_id))
        province = chosen
        fieldwork = ""
        if canonical and canonical == FIELDWORK_PROVINCE:
            if lang == "th":
                fieldwork = f" (หมายเหตุ: แผนลงพื้นที่ภาคสนาม สิงหาคม 2569 ที่{FIELDWORK_DISTRICT} {FIELDWORK_PROVINCE})"
            else:
                fieldwork = f" (Note: our planned August 2026 fieldwork is in {FIELDWORK_DISTRICT}, {FIELDWORK_PROVINCE}.)"
        fallback = _s("confirm_province", lang, province=chosen, fieldwork=fieldwork)
        return _humanize_thai(cfg, fallback=fallback, purpose="confirm province", lang=lang) or fallback

    # No province yet — try to infer from the message
    if not province:
        canonical = _canonical_province(text)
        if canonical or _looks_like_province_name(text):
            chosen = canonical or text.strip()
            conn.execute("UPDATE users SET district = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chosen, user_id))
            province = chosen
            fallback = _s("confirm_province", lang, province=chosen, fieldwork="")
            return _humanize_thai(cfg, fallback=fallback, purpose="confirm province and prompt for loan details", lang=lang) or fallback

        suggestions = _suggest_provinces(text)
        sug_line = f"Did you mean: {', '.join(suggestions)}?\n\n" if suggestions and lang == "en" else (
            f"หมายถึง: {', '.join(suggestions)}?\n\n" if suggestions else ""
        )
        if _looks_like_loan_message(text):
            return _s("loan_no_province", lang, sug=sug_line)
        return _s("ask_province", lang, sug=sug_line)

    # Has province — try to parse loan details
    amount = _parse_amount(text)
    tenure = _parse_tenure(text)
    rate_apr = _parse_rate(text)

    if amount or tenure or rate_apr:
        from .session import load_user_session, save_borrow_draft
        session = load_user_session(conn, user_id=user_id)
        draft_raw = session.get("borrow_draft_json")
        draft: dict[str, Any] = {}
        if draft_raw:
            try:
                draft = dict(json.loads(str(draft_raw)))
            except Exception:
                pass

        if amount:
            draft["amount_inr"] = float(amount)
        if tenure:
            draft["tenure_days"] = int(tenure)
        if rate_apr:
            draft["interest_rate_apr"] = float(rate_apr)

        amt = draft.get("amount_inr")
        ten = draft.get("tenure_days")
        rate = draft.get("interest_rate_apr")

        if amt and ten and rate:
            save_borrow_draft(conn, user_id=user_id, payload=None, source_raw_message_id=None, model=None)
            return _loan_response(cfg, conn=conn, db_path=db_path, province=province,
                                  amount_thb=float(amt), tenure_days=int(ten), rate_apr=float(rate), lang=lang)
        elif amt and ten:
            save_borrow_draft(conn, user_id=user_id, payload=draft, source_raw_message_id=None, model="partial")
            _, total = _simple_interest(float(amt), int(ten), 36.0)
            months = max(1, math.ceil(int(ten) / 30))
            monthly_pay = total / months
            if lang == "th":
                fallback = (
                    f"สำหรับ {_fmt_thb(float(amt))} ระยะเวลา {months} เดือน ที่อัตราสูงสุดพิโกไฟแนนซ์ (36% APR):\n\n"
                    f"- ชำระคืนรวม: ~{_fmt_thb(total)}\n"
                    f"- ชำระต่อเดือน: ~{_fmt_thb(monthly_pay)}\n\n"
                    "ทราบอัตราดอกเบี้ยที่เสนอมาไหมครับ? ส่งมาแล้วผมจะเปรียบเทียบให้แม่นยำขึ้น"
                )
            else:
                fallback = (
                    f"For {_fmt_thb(float(amt))} over {months} month{'s' if months != 1 else ''} at Pico Finance's cap (36% APR):\n\n"
                    f"- Total repayment: ~{_fmt_thb(total)}\n"
                    f"- Monthly payment: ~{_fmt_thb(monthly_pay)}\n\n"
                    "Do you know the interest rate you've been quoted? Send it and I'll compare exactly."
                )
            return _humanize_thai(cfg, fallback=fallback, purpose="show cost estimate and ask for rate", lang=lang) or fallback
        elif amt or ten:
            save_borrow_draft(conn, user_id=user_id, payload=draft, source_raw_message_id=None, model="partial")
            if amt and not ten:
                return _s("need_tenure", lang, amount=_fmt_thb(float(amt)))
            else:
                return _s("need_amount", lang, tenure=str(int(ten)))
        else:
            save_borrow_draft(conn, user_id=user_id, payload=draft, source_raw_message_id=None, model="partial")
            return _suggest_tier_message(conn, province=province, current_rate_apr=float(rate))

    # General prompt
    fallback = _s("prompt_loan", lang, province=province)
    return _humanize_thai(cfg, fallback=fallback, purpose="prompt user for loan details", lang=lang) or fallback


def _loan_response(cfg: Config, *, conn, db_path: str, province: str,
                   amount_thb: float, tenure_days: int, rate_apr: float, lang: str = "en") -> str:
    from ..nudge_content import _simple_interest_estimate, _monthly_repayment_estimate

    monthly_pay, months, total = _monthly_repayment_estimate(amount_thb, tenure_days, rate_apr)
    interest = total - amount_thb

    # Is the rate above the Pico cap?
    PICO_CAP = 36.0
    is_high = rate_apr > PICO_CAP

    if is_high:
        fallback = _alert_tier_message(conn, province=province, quoted_apr=rate_apr,
                                       amount_thb=amount_thb, tenure_days=tenure_days)
        return _recommendation_thai(cfg, fallback=fallback, province=province,
                                    rows=_recommend_tiers(conn, province=province, current_rate_apr=rate_apr),
                                    amount_thb=amount_thb, tenure_days=tenure_days, current_rate=rate_apr,
                                    lang=lang) or fallback
    else:
        monthly_rate = rate_apr / 12.0
        breakdown = (
            f"For {_fmt_thb(amount_thb)} over {months} month{'s' if months != 1 else ''} at {rate_apr:g}% APR (~{_format_percent(monthly_rate)}%/month):\n\n"
            f"- Total interest: ~{_fmt_thb(interest)}\n"
            f"- **Total repayment: ~{_fmt_thb(total)}**\n"
            f"- Estimated monthly payment: ~{_fmt_thb(monthly_pay)}\n\n"
            "*Simple interest estimate — actual fees may vary.*"
        )
        tier_list = _suggest_tier_message(conn, province=province, current_rate_apr=rate_apr,
                                          amount_thb=amount_thb, tenure_days=tenure_days)
        combined = f"{breakdown}\n\n---\n\n{tier_list}"
        return _recommendation_thai(cfg, fallback=combined, province=province,
                                    rows=_recommend_tiers(conn, province=province, current_rate_apr=rate_apr),
                                    amount_thb=amount_thb, tenure_days=tenure_days, current_rate=rate_apr,
                                    lang=lang) or combined


def _looks_like_loan_message(text: str) -> bool:
    low = text.lower()
    loan_words = ["loan", "borrow", "need", "lend", "baht", "฿", "interest", "repay", "monthly", "week", "day"]
    return any(w in low for w in loan_words) or bool(re.search(r"\d", text))


def _extract_province_command(text: str) -> str | None:
    patterns = [
        r"(?:i(?:'m| am) in|set province|my province is|province[:\s]+|i(?:'m| am) from)\s+([a-zA-Z\s\-]+)",
        r"(?:from|in)\s+([A-Z][a-zA-Z\s\-]{2,30})(?:\s|$|,|\.|province)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if _canonical_province(candidate) or _looks_like_province_name(candidate):
                return candidate
    return None
