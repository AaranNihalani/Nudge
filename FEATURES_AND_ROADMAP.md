# NudgeAI: Current Features + Roadmap

## Is it actually using the Claude API?
Yes, if `CLAUDE_API_KEY` (or `ANTHROPIC_API_KEY`) is set in your environment.

What Claude is used for right now:
- Borrow-intent parsing only: turning free-text WhatsApp messages into a strict JSON record (`parsed_events`) with fields like amount, tenure, implied APR, lender type, and negotiation stage.
- This parsing runs only when the user is opted-in, has a district set, and the message is not a command.

What Claude is not used for right now:
- It is not generating “financial advice” text. Advice/suggestions are generated deterministically from (a) the MFI database and (b) the baseline/RL policy logic.

How to verify Claude is being called:
1. Ensure `CLAUDE_API_KEY` is set on your deployed service.
2. WhatsApp: `START` → set a district → send a loan message with terms.
3. Check the DB for a new `parsed_events` row with a non-empty `model` value.

Example DB check (local DB file path will differ on Render if you mount a disk):
```bash
python - << 'PY'
import sqlite3
db = "./data/nudge.sqlite3"
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row
rows = c.execute(
  "select id, event_type, intent, confidence, amount_inr, tenure_days, interest_rate_apr, lender_type, negotiation_stage, model "
  "from parsed_events order by id desc limit 5"
).fetchall()
for r in rows:
  print(dict(r))
PY
```

## Current Features (Today)

### WhatsApp bot basics
- Twilio webhook: receives inbound messages and replies immediately (TwiML response).
- Opt-in/out: `START` opts in, `STOP` opts out.
- Help: `HELP` returns command guidance.

### District onboarding and discovery
- District set/change: `DISTRICT <name>` (also supports “set/change district …”).
- District discovery: `DISTRICTS` and `DISTRICTS <prefix>` return a (paged) list; `MORE` continues listing.
- Language selection: `LANG EN|HI|HINGLISH` (stored per user session).

### MFI database (regulated alternatives)
- Loads a lender/rate dataset (CSV/JSON) into SQLite.
- Queries regulated alternatives for a district and provides deterministic top-N suggestions.

### Advice/suggestions behavior
- Safe-default suggestion: if the user is opted in + district set and message limits allow it, the bot can return “suggest lender” guidance using the MFI database.
- Baseline policy mode: if enabled, the bot computes state features from stored history + parsed events and selects an action from {wait, alert, suggest_lender, education}.
- Verbose parse echo: when `NUDGE_VERBOSE_REPLIES=true`, replies include parsed loan terms + missing fields and how to correct them.
- Corrections: `CORRECT <field>=<value>` updates the current draft or most recent parsed loan.
- Clarifying questions: if the loan is missing amount/tenure/rate, the bot asks one follow-up and resumes automatically.
- Outcome signals: `CONTACTED <lender>` and `SWITCHED <lender>` record self-reported actions for pilot measurement.

### RL research pipeline (offline)
- Synthetic environment + trajectory generator (target 10,000 users).
- PPO training (Stable-Baselines3) and evaluation vs baseline.
- Reward shaping ablations runner.

### Admin operations
- Daily decision runner (manual trigger) and idempotent runs.
- Anonymized metrics export for paper plots (no phone numbers, no raw message bodies).

## Desirable Features (To make it genuinely useful for real people)

### 1) Better “Start” experience (high impact)
Goal: after `START`, a user should immediately understand what to send and get useful, local options quickly.

Desirable improvements:
- Show a short “what to send” template for loan terms in Hinglish/Hindi and English.
- Offer guided district selection (“type first 2–3 letters”) and paginate results.
- Confirm what the bot can/can’t do (not a lender, not a guarantee, informational only).

Status:
- Implemented: district paging via `MORE`, and `LANG` command (user-level session setting).
- Remaining for “fully polished”: stronger bilingual templates throughout all messages, better district pagination UX (page numbers / shorter chunks), and district matching that prefers official district spellings.

### 2) Explainability in every suggestion
Goal: suggestions should include “why you’re seeing this” and a simple comparison.

Desirable improvements:
- Normalize interest formats (monthly/weekly/daily) into a single APR explanation.
- Include “cost of borrowing” rough estimate in INR given amount + time.
- Include what makes a lender “regulated” in one line.

Status:
- Implemented: APR is clearly labelled as annualised, includes ≈monthly equivalent, plus a simple “total repay” estimate when amount+tenure are known, plus a short “why regulated” line.
- Remaining: richer lender metadata (links/phone/address/RBI registration) in the dataset and in messages.

### 3) Safer real-world interactions
Goal: prevent harm and build trust.

Desirable improvements:
- Robust “STOP” handling and confirmation in multiple languages.
- “HELP” includes escalation (“talk to a human” / support contact) for pilots.
- Rate-limit and abuse detection (spam, harassment, unrelated content).

Status:
- Implemented: optional support contact appended to HELP via `NUDGE_SUPPORT_CONTACT`, plus hard cooldown/cap limits.
- Remaining: explicit retention tooling, abuse detection, and stronger privacy controls for production pilots.

### 4) “Advice” depth (beyond lender list)
Goal: provide actionable next steps, not just names.

Desirable improvements:
- Ask 1 clarifying question if critical fields are missing (amount/tenure/rate).
- Provide a step-by-step script: what to ask the MFI, what documents might be needed, what fees to watch for.
- Add a “switch confirmation” flow (“Did you contact X? Did you switch?”) to measure outcomes.

Status:
- Implemented: deterministic dialog state for missing fields (amount/tenure/rate), plus `CONTACTED` and `SWITCHED` outcome capture.
- Remaining: a more complete step-by-step “what to ask / what documents” script, and a richer outcome pipeline (applied/approved/repayment).

### 5) RL policy that can run safely in production
Goal: move from baseline rules to an RL policy that is safe, auditable, and robust.

Desirable improvements:
- Model version management with staged rollout (A/B test baseline vs RL).
- Guardrails: RL decisions filtered by hard safety constraints (cooldown, max nudges, opt-out risk).
- Monitoring: drift and unexpected behavior alerts.

Status:
- Implemented: `NUDGE_POLICY_MODE=auto` can stage RL rollout via `NUDGE_RL_ROLLOUT_PCT` (stable per-user split) with safe fallback to baseline.
- Remaining: stronger monitoring/alerting, and explicit “hard” safety filtering on RL actions beyond the existing cooldown/caps.

## What you must do to achieve “fully functioning for real users” (non-code + code)

### Non-code (required)
- A real MFI dataset per district with contacts/links and rates that are kept up to date.
- A deployment that stays awake (free tiers may sleep, which breaks the “right moment” nudge).
- A consent + privacy policy you can show users (pilot-grade).
- A pilot ops plan: support contact, escalation, how you recruit, how you handle opt-outs.

### Code (recommended next steps)
1. Expand MFI schema (contacts/URLs/addresses, lender type, source metadata).
2. Improve message formatting and bilingual templates.
3. Add the “missing info” clarifying question flow before any recommendation.
4. Add switch confirmation and outcome measurement flows.
5. Add production RL guardrails + A/B testing harness.
