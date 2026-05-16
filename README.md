# Nudge Webhook Service

Minimal Flask service for receiving Twilio webhooks (e.g. WhatsApp) and optionally generating responses via Claude.

## Endpoints

- `GET /health` → JSON health check
- `POST /twilio` → Twilio Messaging webhook (expects `application/x-www-form-urlencoded`)

## Environment

Create/update a `.env` file at the project root and fill in values as needed.

- `PORT` (default `5000`)
- `NUDGE_DB_PATH` (optional; default `./data/nudge.sqlite3`)
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_VALIDATE_SIGNATURE` (`true`/`false`, default `true`)
- `CLAUDE_API_KEY` (or `ANTHROPIC_API_KEY`)
- `CLAUDE_MODEL` (default `claude-3-5-sonnet-latest`)

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

This repo includes a `Procfile` for Gunicorn:

```
web: gunicorn nudge_webhook.wsgi:app --bind 0.0.0.0:$PORT
```

On startup, the service initializes the SQLite DB and runs schema migrations automatically. Twilio requires a publicly reachable HTTPS URL for `POST /twilio` (any host that can run Flask/Gunicorn works).

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
