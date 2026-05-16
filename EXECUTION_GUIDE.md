# NudgeAI Execution Guide (Exact Steps)

This is a step-by-step runbook to validate and operate the repo end-to-end.

## 0) One-time setup

1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Create/update `.env` (project root) and fill values

Minimum to run locally:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (optional for local replay tests)
- `CLAUDE_API_KEY` (only needed for real parsing; tests stub the call)

4. Start the server

```bash
python -m nudge_webhook
```

5. Health check

```bash
curl http://localhost:5050/health
```

## 1) Load MFI data into SQLite

1. Ensure `datasets/mfi_rates.csv` contains your districts, lenders, and `rate_apr`.
2. Load into the DB using the CLI

```bash
source .venv/bin/activate
export FLASK_APP=nudge_webhook.wsgi
flask load-mfi --dataset datasets/mfi_rates.csv
```

3. Confirm districts are available

```bash
curl "http://localhost:5050/mfi/districts"
```

## 2) Verify WhatsApp bot flows (local HTTP replay)

You can replay Twilio-like requests locally without Twilio.

1. Opt-in

```bash
curl -X POST http://localhost:5050/twilio \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "From=whatsapp:+15551230001" \
  --data-urlencode "Body=START"
```

2. Set district (if prompted)

```bash
curl -X POST http://localhost:5050/twilio \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "From=whatsapp:+15551230001" \
  --data-urlencode "Body=Lucknow"
```

3. Send a borrow-intent message

```bash
curl -X POST http://localhost:5050/twilio \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "From=whatsapp:+15551230001" \
  --data-urlencode "Body=Need 5000 for 30 days. Moneylender says 5% monthly."
```

4. Opt-out

```bash
curl -X POST http://localhost:5050/twilio \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "From=whatsapp:+15551230001" \
  --data-urlencode "Body=STOP"
```

## 3) Run the full test suite (includes end-to-end fixture replay)

```bash
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

## 4) Generate synthetic trajectories (10,000 users)

```bash
mkdir -p ./artifacts
./.venv/bin/python -m nudge_webhook.synthetic_trajectories \
  --output ./artifacts/synthetic_10000.jsonl \
  --users 10000 \
  --days 120 \
  --seed 0
```

## 5) Train PPO (CPU) and evaluate vs baseline

1. Install RL dependencies (separate from production deps)

```bash
./.venv/bin/pip install -r requirements-rl.txt
```

2. Train PPO and write outputs under `./runs/`

```bash
./.venv/bin/python -m nudge_webhook.rl_train_ppo \
  --timesteps 50000 \
  --days 120 \
  --seed 0
```

3. Evaluate PPO vs baseline on the same deterministic cohort

```bash
./.venv/bin/python -m nudge_webhook.rl_eval \
  --model ./runs/<RUN_DIR>/model.zip \
  --users 2000 \
  --days 120 \
  --seed 42
```

4. Run reward shaping ablations

```bash
./.venv/bin/python -m nudge_webhook.rl_ablations \
  --presets default,no_spam,no_engagement \
  --timesteps 30000
```

## 6) Policy serving modes (baseline vs RL)

Set in `.env`:
- Baseline: `NUDGE_POLICY_MODE=baseline`
- RL (explicit): `NUDGE_POLICY_MODE=rl` and set `NUDGE_RL_MODEL_PATH=./models/model.zip` (or set an active version if you use versioned directories)
- Auto: `NUDGE_POLICY_MODE=auto` (tries RL, falls back to baseline)

## 7) Daily decisions and metrics export

1. Run daily decisions manually (no external scheduler required)

```bash
./.venv/bin/python -m nudge_webhook.admin_cli run-daily
```

2. Export anonymized metrics as a zip (paper-ready aggregates)

```bash
mkdir -p ./artifacts
./.venv/bin/python -m nudge_webhook.admin_cli export-metrics --out ./artifacts/nudge_metrics.zip
```

## 8) Deploy (minimal cost)

Twilio requires a publicly reachable HTTPS webhook URL. Low-cost options:
- Run on a small VPS and point Twilio webhook to `https://<your-domain>/twilio`
- Run on your machine and expose via a tunnel (for short pilots)
