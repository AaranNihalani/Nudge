# Nudge Webhook Service

Flask service that receives Twilio webhooks (WhatsApp/SMS), parses a user’s loan context, and replies with regulated local alternatives plus clear “how much you’d pay” estimates.

It also ships with a lightweight web chat UI at `/` for quick testing.

## Endpoints

- `GET /health` → JSON health check
- `POST /twilio` → Twilio Messaging webhook (expects `application/x-www-form-urlencoded`)
- `GET /` → Web chat UI
- `POST /api/chat` → Web chat API (JSON)

## Environment

Create/update a `.env` file at the project root and fill in values as needed.

### Required for Twilio (production)

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_VALIDATE_SIGNATURE` (`true`/`false`, default `true`)
- `TWILIO_FROM` or `TWILIO_FROM_ADDR` (optional; used in some outbound flows)

### Claude (recommended)

Claude is used to make messages more natural and to parse free-form loan descriptions when available.

- `CLAUDE_API_KEY` (or `ANTHROPIC_API_KEY`)
- `CLAUDE_MODEL` (default `claude-3-5-sonnet-latest`)
- `NUDGE_CLAUDE_TIMEOUT_SECONDS` (default `8`, clamped to `1..12`)
- `NUDGE_CLAUDE_ATTEMPTS` (default `1`, clamped to `1..3`)
- `NUDGE_DEBUG_CLAUDE` (`true`/`false`, default `false`) — raises Claude errors instead of silently falling back (useful for debugging)

### Database

- `NUDGE_DB_PATH` (optional)
- `SQLITE_PATH` (optional legacy alias)

Notes:
- On Vercel/serverless, the filesystem under the code directory is read-only. If `NUDGE_DB_PATH` points to a read-only location, the app automatically falls back to a SQLite DB under `/tmp/`.
- SQLite in `/tmp/` is ephemeral on serverless platforms. Expect state to reset between cold starts; use a persistent DB if you need durable storage.

### Policy / throttling (optional)

- `NUDGE_BASELINE_POLICY_ENABLED` (`true`/`false`, default `false`)
- `NUDGE_POLICY_MODE` (`off|baseline|rl|auto`; defaults to `baseline` if baseline is enabled, otherwise `off`)
- `NUDGE_COOLDOWN_MINUTES` (default `360`)
- `NUDGE_MAX_PER_DAY` (default `2`)
- `NUDGE_MAX_PER_WEEK` (default `5`)

### Dataset (optional)

- `NUDGE_MFI_DATASET_PATH` (default `./datasets/mfi_rates.csv`)
- `NUDGE_MFI_AUTOLOAD` (`true`/`false`, default `true`)

### Web

- `PORT` (default `5000`)

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m nudge_webhook
```

Health check:

```bash
curl http://localhost:5000/health
```

## Web chat UI

Open:

- `http://localhost:5000/`

The UI renders basic markdown safely:

- `**bold**`
- `---` separators
- `- bullet` lines
- `https://...` links

The sidebar shows the last parsed loan (or draft parse) and whether Claude is configured (`claude: on/off`).

## Local validation (Task 10)

Run the full local test suite (includes an end-to-end smoke flow that replays recorded Twilio webhook payload fixtures):

```bash
source .venv/bin/activate
python -m unittest discover -s tests -p "test_*.py"
```

Run just the Task 10 end-to-end smoke test:

```bash
source .venv/bin/activate
python -m unittest tests.test_task10_e2e_local_fixtures
```

Recorded webhook fixtures live under `tests/fixtures/twilio/` and are replayed via Flask's test client against `POST /twilio`.

## Deployment

### Gunicorn (Render/Railway/etc.)

This repo includes a `Procfile` for Gunicorn:

```
web: gunicorn nudge_webhook.wsgi:app --bind 0.0.0.0:$PORT
```

On startup, the service initializes the SQLite DB and runs schema migrations automatically. Twilio requires a publicly reachable HTTPS URL for `POST /twilio` (any host that can run Flask/Gunicorn works).

### Vercel (serverless Python)

This repo includes a top-level [app.py](file:///Users/aarannihalani/GitHub/Nudge/app.py) entrypoint for Vercel’s Python runtime.

Important notes for Vercel:

- Set `CLAUDE_API_KEY` (or `ANTHROPIC_API_KEY`) in Vercel Project Settings → Environment Variables.
- Do not use a `NUDGE_DB_PATH` under the code directory (read-only). If you leave it unset, the app uses `/tmp/nudge.sqlite3` automatically.
- Expect the SQLite DB to be ephemeral on serverless; don’t rely on it for durable history.

## RL training (Task 8)

Install extra dependencies (kept separate from production runtime):

```bash
source .venv/bin/activate
pip install -r requirements-rl.txt
```

Train PPO (saves `runs/.../model.zip` + `train_config.json` + SB3 CSV logs):

```bash
python -m nudge_webhook.rl_train_ppo --timesteps 50000 --days 120 --reward default
```

Evaluate PPO vs baseline on the same deterministic synthetic set (saves `metrics.json` + per-user CSVs):

```bash
python -m nudge_webhook.rl_eval --model runs/<run>/model.zip --users 2000 --days 120 --reward default
```

Run reward shaping ablations (trains + evaluates per preset under `runs/.../ablations_*/`):

```bash
python -m nudge_webhook.rl_ablations --presets default,no_spam,no_engagement --timesteps 30000
```
