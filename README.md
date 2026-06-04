# Nudge

Nudge is an English-only, research-informed lending guidance assistant for India. It helps a user:

- Describe a loan they are considering (amount, time period, lender type, optional interest quote)
- Understand “what you would actually pay” under a transparent, clearly stated set of assumptions
- Compare against local regulated alternatives using a district-level microfinance (MFI) APR dataset
- Get a research-backed “credit access profile” based on AIDIS 2019 (optional profile questions)

This repository is built as a Flask web service with a web chat UI, a structured parsing pipeline, a baseline policy, and optional Claude-powered message rewriting.

## Research basis (AIDIS 2019)

The profile module contains a conservative, citation-preserving assessment flow based on reported average marginal effects (AMEs) from AIDIS 2019 (as described in the module docstring):

- Nihalani, A. (2025). *Understanding Financial Inclusion: Patterns and Determinants of Formal Borrowing in India.* SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6006354

Important: the assessment reports per-factor effects only and does not aggregate them into a single score.

## Product behavior (what the bot will and won’t claim)

- **Payment estimates**: uses APR-only *simple interest* for transparency and fast mental math. It does not model reducing-balance EMI schedules unless explicitly stated.
- **Fees and add-ons**: estimates exclude processing fees, insurance, penalties, foreclosure charges, and lender-specific amortization rules.
- **Contact details**: shows phone/email only when present in the local verified contact directory; it does not invent numbers.
- **Safety**: does not claim approvals or eligibility; it recommends confirming the exact EMI/total repayment in writing with the lender.

## API surface

**Public routes**

- `GET /` — landing page
- `GET /chat` — web chat UI (calls the same state machine as the API)
- `POST /api/chat` — web chat API
- `GET /health` — health check + DB + dataset status

**Data routes (MFI)**

- `GET /mfi/districts`
- `GET /mfi/rates?district=<name>`
- `GET /mfi/alternatives?district=<name>&current_rate=<apr>&n=3`

**Admin routes**

- `GET /admin/export-metrics` — anonymised metrics bundle (requires `X-Admin-Token`)

### `POST /api/chat` example

```bash
curl -sS http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"Need 5000 for 30 days with moneylender"}'
```

## Codebase map

```text
app.py                      Vercel entrypoint (imports nudge_webhook.wsgi:app)
vercel.json                 Vercel build config

nudge_webhook/
  app.py                    Flask app factory and HTTP routes
  config.py                 Config (env-driven)
  db.py                     SQLite schema + migrations + connection helpers
  mfi.py                    MFI dataset loading + /mfi/* API
  nlp.py                    JSON-schema borrow intent parsing (Claude or stubbed)
  nudge_content.py          Payment math + message templates + lender contacts
  lender_contacts.json      Verified lender phone/email/website directory
  policy.py                 Baseline policy (alert/suggest/education/wait)
  state.py                  UserState builder (DB → state features)
  metrics_export.py         Anonymised metrics export bundle
  claude.py                 Claude API wrapper (retries/timeouts)
  wsgi.py                   WSGI entrypoint for Gunicorn

  bot/
    handler.py              Main conversation orchestrator
    parsers.py              Text parsing utilities (amount/tenure/rate/commands)
    loan.py                 Option selection + loan-related helpers
    session.py              Per-user session storage (drafts, options)
    profile.py              AIDIS-informed profile flow + assessment
    claude_helpers.py       Claude rewrite helpers (safe, preserve facts)

  static/                   Web chat assets
  templates/                landing + chat UI templates
```

## Configuration

Create a `.env` file at the project root (optional locally). Key variables:

**Core**

- `PORT` (default `5000`)
- `NUDGE_DB_PATH` (optional; default `./data/nudge.sqlite3`, or `/tmp/nudge.sqlite3` on Vercel)
- `NUDGE_MFI_DATASET_PATH` (default `./datasets/mfi_rates.csv`)
- `NUDGE_MFI_AUTOLOAD` (`true`/`false`, default `true`)

**Claude (recommended)**

- `CLAUDE_API_KEY` (or `ANTHROPIC_API_KEY`)
- `CLAUDE_MODEL` (default `claude-sonnet-4-6`)
- `NUDGE_CLAUDE_TIMEOUT_SECONDS` (default `30`, clamped to `1..60`)
- `NUDGE_CLAUDE_ATTEMPTS` (default `1`, clamped to `1..3`)
- `NUDGE_DEBUG_CLAUDE` (`true`/`false`, default `false`)

**Baseline policy**

- `NUDGE_BASELINE_POLICY_ENABLED` (`true`/`false`, default `false`)
- `NUDGE_POLICY_MODE` (`off|baseline|auto`; default `off` unless baseline is enabled)
- `NUDGE_COOLDOWN_MINUTES`, `NUDGE_MAX_PER_DAY`, `NUDGE_MAX_PER_WEEK`

**Admin / export**

- `NUDGE_ADMIN_TOKEN` (required for admin endpoints)
- `NUDGE_ANON_SALT` (recommended; used to anonymise exports)

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m nudge_webhook
```

Then open:

- `http://localhost:5000/chat`

## Deployment

### Vercel (serverless Python)

- Entrypoint: `app.py` (repo root)
- Set environment variables in Vercel Project Settings.
- The code directory is read-only on Vercel; SQLite must live under `/tmp/` (the app will fall back automatically when needed).

SQLite on `/tmp/` is ephemeral on serverless platforms; do not rely on it for durable state across cold starts.

### Render / Railway / any VM (Gunicorn)

The repo includes a `Procfile`:

```text
web: gunicorn nudge_webhook.wsgi:app --bind 0.0.0.0:$PORT
```

## Testing

```bash
source .venv/bin/activate
python -m unittest discover -s tests -p "test_*.py"
```

## Notes on Twilio / WhatsApp

This codebase focuses on a web-first chat surface (`/chat`, `/api/chat`). The conversation core (`InboundMessage`, `process_inbound`) is written so it can be adapted to a Twilio webhook, but a `/twilio` endpoint and signature validation are not included in the current server.
