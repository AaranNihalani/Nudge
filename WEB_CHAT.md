# Web Chat Console

This repository includes a first‑party web UI at the website root (`/`) that lets you interact with the Nudge bot without WhatsApp/Twilio. It runs the same conversation/state logic as the Twilio webhook, but adds an operator-grade debug surface so you can validate parsing and policy decisions.

## What It Is
- **UI:** `GET /` serves a full‑screen chat console.
- **API:** `POST /api/chat` accepts a message and returns:
  - `reply`: the bot message (same content as WhatsApp)
  - `debug`: structured metadata (consent, district, policy decision, parsing artifacts, MFI coverage)

## Session Model
The web UI generates a stable `session_id` (stored in `localStorage`) and sends it with every request.
On the backend the chat adapter maps this to a pseudo phone number:
- `phone_e164 = web:<session_id>`

This means the same DB-backed state machine works unchanged (consent, district selection, message history, nudges, parsed_events, etc.).

## Debug Semantics (what to look at)
The right rail shows:
- **Context:** consent status, current district, number of loaded MFI districts, active policy decision.
- **Parsed Loan:** the latest `borrow_intent` event (amount/tenure/lender_type/stage/model, plus APR if supplied).
- **Debug JSON:** raw `debug` payload returned by the API.

Key fields:
- `parsed`: `yes` / `attempted` / `no`
- `decision`: policy outcome (e.g., `wait`, `suggest_lender`, `alert`)
- `last_borrow_intent`: what the system believes you said

### Lender Type vs CONTACTED/SWITCHED
- `lender_type` describes the *current lender you are considering* (e.g., `moneylender`, `shopkeeper`, `friend_family`).
- `CONTACTED <lender>` and `SWITCHED …` are outcome tracking commands stored as `user_actions`; they do not mutate the original loan event.

## Endpoints
- `GET /` – web chat UI
- `POST /api/chat` – web chat API
- `POST /twilio` – Twilio inbound webhook (WhatsApp/SMS)
- `GET /health` – health + DB path + MFI district count

## Render Deployment Notes
- If you want DB persistence, mount a disk and set:
  - `NUDGE_DB_PATH=/var/data/nudge.sqlite3`
- Auto-load MFI data at startup:
  - `NUDGE_MFI_AUTOLOAD=true`
  - `NUDGE_MFI_DATASET_PATH=./datasets/mfi_rates.csv`
- Recommended debugging for pilots:
  - `NUDGE_VERBOSE_REPLIES=true`
