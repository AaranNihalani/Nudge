# Nudge — Post-Deploy Setup Checklist

Complete these steps after the code changes are pushed and deployed.

---

## 1. Supabase (Free PostgreSQL — Persistent Storage)

- [X] Go to **supabase.com** → Sign up → New Project → choose name + strong password
- [X] Wait ~2 min for provisioning
- [X] Go to **Settings → Database → Connection string → URI** tab
- [X] Copy the URI: `postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres`
- [X] Save this as `DATABASE_URL` (used in Vercel step)

> The app auto-creates all tables on first boot — no manual SQL needed.

---

## 2. Anthropic (Claude API)

- [X] Go to **console.anthropic.com** → API Keys → Create Key
- [X] Save this as `CLAUDE_API_KEY`

---

## 3. Twilio — Full Production WhatsApp (not sandbox)

This takes **3–7 days** due to Meta's verification process. Start early.

### Step A — Twilio Account

- [ ] Go to **console.twilio.com** → sign up or log in (paid tier required for production)
- [ ] Note your **Account SID** and **Auth Token** from the dashboard home

### Step B — Meta Business Account

- [ ] Go to **business.facebook.com** → create a Meta Business Account if you don't have one
- [ ] Verify your business (may require documents)

### Step C — WhatsApp Business API via Twilio

- [ ] In Twilio Console: **Messaging → Senders → WhatsApp Senders → Get Started**
- [ ] Follow the flow to connect your Meta Business Account to Twilio
- [ ] Choose or purchase a phone number for WhatsApp
- [ ] Submit your business profile for WhatsApp approval (Meta reviews this)
- [ ] Wait for approval email (typically 1–5 business days)

### Step D — Message Templates (required for outbound/proactive messages)

- [ ] In Twilio Console: **Messaging → Content Template Builder → Create New Template**
- [ ] Create a template for your nudge messages (e.g. "Hi {{1}}, Nudge here. You mentioned a loan from {{2}} at {{3}}% APR...")
- [ ] Submit each template to Meta for approval (24–48 hrs per template)
- [ ] Note the template SIDs once approved

### Step E — Configure Webhook (after deployment)

- [ ] In Twilio Console: **Phone Numbers → Manage → Active Numbers → your number**
- [ ] Under Messaging: set **"When a message comes in"** → Webhook → `https://YOUR-VERCEL-URL.vercel.app/twilio` → HTTP POST
- [ ] Save

---

## 4. Vercel (Deployment)

- [X] Go to **vercel.com** → New Project → Import GitHub repo (`Nudge`)
- [X] Framework preset: **Other**
- [X] Root directory: leave blank (/)
- [X] Click **Deploy** (will fail without env vars — add them next)
- [X] Go to **Project Settings → Environment Variables** and add all of these:

| Variable                          | Value                         | Source                                  |
| --------------------------------- | ----------------------------- | --------------------------------------- |
| `DATABASE_URL`                  | `postgresql://postgres:...` | Supabase → Settings → Database → URI |
| `CLAUDE_API_KEY`                | `sk-ant-...`                | console.anthropic.com                   |
| `TWILIO_ACCOUNT_SID`            | `ACxxxxxxxxxxxxxxxx`        | Twilio dashboard                        |
| `TWILIO_AUTH_TOKEN`             | `xxxxxxxxxxxxxxxx`          | Twilio dashboard                        |
| `TWILIO_VALIDATE_SIGNATURE`     | `true`                      | (type it)                               |
| `TWILIO_FROM`                   | `whatsapp:+1xxxxxxxxxx`     | Your approved Twilio WhatsApp number    |
| `NUDGE_MFI_AUTOLOAD`            | `true`                      | (type it)                               |
| `NUDGE_MFI_DATASET_PATH`        | `./datasets/mfi_rates.csv`  | (type it)                               |
| `NUDGE_BASELINE_POLICY_ENABLED` | `true`                      | (type it)                               |
| `NUDGE_ADMIN_TOKEN`             | (any strong secret)           | Make one up — keep it safe             |

- [X] Redeploy after adding env vars

---

## 5. Wire Twilio to Your Live URL

- [ ] Copy your Vercel URL (e.g. `https://nudge-xyz.vercel.app`)
- [ ] Back in Twilio → your WhatsApp number → Messaging webhook → update to `https://nudge-xyz.vercel.app/twilio`
- [ ] Save

---

## 6. Verify Everything Is Working

- [ ] `https://nudge-xyz.vercel.app/` → professional landing page loads
- [ ] `https://nudge-xyz.vercel.app/chat` → chat UI loads
- [ ] `https://nudge-xyz.vercel.app/health` → JSON response with `"status": "ok"` and `mfi_districts` > 0
- [ ] WhatsApp: send `START` to your Twilio number → bot replies with onboarding message
- [ ] Web chat: type `START` → bot replies

---

## 7. Admin Endpoints

Two admin endpoints are protected by `X-Admin-Token: <NUDGE_ADMIN_TOKEN>`:

```bash
# Manually trigger daily nudge decisions
curl -X POST https://nudge-xyz.vercel.app/admin/run-daily \
  -H "X-Admin-Token: your-admin-token"

# Download anonymised metrics ZIP for research
curl https://nudge-xyz.vercel.app/admin/export-metrics \
  -H "X-Admin-Token: your-admin-token" \
  -o metrics.zip
```

---

## 8. Local Development (Optional)

Add to your `.env` file (already gitignored):

```
DATABASE_URL=postgresql://postgres:...    # Supabase URI
CLAUDE_API_KEY=sk-ant-...
TWILIO_VALIDATE_SIGNATURE=false           # Skip signature check locally
NUDGE_BASELINE_POLICY_ENABLED=true
NUDGE_MFI_AUTOLOAD=true
```

Run:

```bash
source .venv/bin/activate
python -m nudge_webhook
```

> If `DATABASE_URL` is not set, the app falls back to local SQLite automatically.
