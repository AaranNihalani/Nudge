# Nudge

AI-powered financial guidance for fairer borrowing in India.

Nudge is a WhatsApp and web chatbot that helps households understand their credit options, compare regulated MFI lenders, and avoid predatory informal loans. It is informed by peer-reviewed research using the 2019 All India Debt and Investment Survey (AIDIS).

**Research:** Nihalani, A. (2025). *Understanding Financial Inclusion: Patterns and Determinants of Formal Borrowing in India.* SSRN. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6006354

---

## Routes

| Route | Description |
|---|---|
| `GET /` | Professional landing page |
| `GET /chat` | Web chat UI (same logic as WhatsApp) |
| `POST /api/chat` | Web chat API (JSON) |
| `POST /twilio` | Twilio Messaging webhook (WhatsApp/SMS) |
| `GET /health` | Health check + DB and MFI status |
| `POST /admin/run-daily` | Trigger daily nudge runner (requires `X-Admin-Token`) |
| `GET /admin/export-metrics` | Download anonymised metrics ZIP (requires `X-Admin-Token`) |

---

## Codebase structure

```
nudge_webhook/
  app.py               Flask app factory and routes
  config.py            Config dataclass (loaded from env vars)
  db.py                DB layer — SQLite (local) or PostgreSQL (DATABASE_URL)
  state.py             UserState computation from DB
  policy.py            Nudge policy — decides when and what to send proactively
  nudge_content.py     MFI rate lookup and lender message formatting
  claude.py            Claude API integration
  nlp.py               Borrow intent extraction
  mfi.py               MFI dataset loading into SQLite/PostgreSQL
  daily_runner.py      Daily proactive nudge runner
  twilio_outbound.py   Outbound Twilio messaging
  metrics_export.py    Anonymised metrics export
  admin_cli.py         Admin CLI tool

  bot/                 Conversation handler (split into focused modules)
    __init__.py        Exports: InboundMessage, process_twilio_inbound
    handler.py         Main orchestration
    helpers.py         Shared utilities (timestamps, normalization)
    parsers.py         Text parsing (amounts, tenures, rates, commands)
    session.py         Per-user session state management
    loan.py            Loan payload logic and lender option selection
    profile.py         Profile collection and AIDIS credit access assessment
    claude_helpers.py  Claude message humanisation helpers
```

---

## Environment variables

### Required for production

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URI (e.g. from Supabase) |
| `CLAUDE_API_KEY` | Anthropic API key |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM` | Your WhatsApp-enabled Twilio number (`whatsapp:+1...`) |
| `NUDGE_ADMIN_TOKEN` | Secret token for admin endpoints |

### Recommended

| Variable | Default | Description |
|---|---|---|
| `TWILIO_VALIDATE_SIGNATURE` | `true` | Validate Twilio webhook signatures |
| `NUDGE_BASELINE_POLICY_ENABLED` | `false` | Enable proactive nudge policy |
| `NUDGE_MFI_AUTOLOAD` | `true` | Auto-load MFI dataset on startup |
| `NUDGE_MFI_DATASET_PATH` | `./datasets/mfi_rates.csv` | Path to MFI rates CSV |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model to use |

### Optional

| Variable | Default | Description |
|---|---|---|
| `NUDGE_COOLDOWN_MINUTES` | `360` | Minimum minutes between nudges per user |
| `NUDGE_MAX_PER_DAY` | `2` | Max nudges per user per day |
| `NUDGE_MAX_PER_WEEK` | `5` | Max nudges per user per week |
| `NUDGE_VERBOSE_REPLIES` | `false` | Include debug info in replies |
| `NUDGE_DEBUG_CLAUDE` | `false` | Raise Claude errors instead of silently falling back |

---

## Database

The app auto-creates and migrates its schema on startup — no manual SQL needed.

- **Local dev / no DATABASE_URL:** SQLite at `./data/nudge.sqlite3` (or `/tmp/nudge.sqlite3` on Vercel)
- **Production:** PostgreSQL via `DATABASE_URL` (recommended: Supabase free tier)

If `NUDGE_DB_PATH` points to a read-only location (e.g. Vercel serverless), the app falls back to `/tmp/nudge.sqlite3` automatically.

---

## WhatsApp setup (production)

See [TODO.md](TODO.md) for the full step-by-step checklist, including Twilio WhatsApp Business API setup, Meta business verification, and message template approval.

---

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m nudge_webhook
```

Add a `.env` file at the project root:

```
CLAUDE_API_KEY=sk-ant-...
TWILIO_VALIDATE_SIGNATURE=false
NUDGE_BASELINE_POLICY_ENABLED=true
NUDGE_MFI_AUTOLOAD=true
```

Health check:

```bash
curl http://localhost:5000/health
```

---

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -p "test_*.py"
```

---

## Deployment

### Vercel (serverless)

This repo includes a top-level `app.py` entrypoint for Vercel's Python runtime. Set all environment variables in Vercel Project Settings → Environment Variables.

### Render / Railway (Gunicorn)

```
web: gunicorn nudge_webhook.wsgi:app --bind 0.0.0.0:$PORT
```

---

## Admin endpoints

Both require `X-Admin-Token: <NUDGE_ADMIN_TOKEN>` header.

```bash
# Trigger daily nudge decisions manually
curl -X POST https://your-app/admin/run-daily \
  -H "X-Admin-Token: your-token"

# Download anonymised metrics ZIP
curl https://your-app/admin/export-metrics \
  -H "X-Admin-Token: your-token" \
  -o metrics.zip
```

---

## Citation

```
Nihalani, A. (2025). Understanding Financial Inclusion: Patterns and Determinants
of Formal Borrowing in India. SSRN.
https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6006354
```
