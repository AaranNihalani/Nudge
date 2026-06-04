# Nudge — Remaining Setup Checklist

Everything below is what's still left to complete before Nudge is fully live on WhatsApp.

> **Note:** No message templates are needed. Nudge only replies to messages users send to it (within WhatsApp's 24-hour free-form window). There are no proactive outbound nudges.

---

## 1. Twilio — Production WhatsApp

This takes **3–7 days** due to Meta's review process. Start immediately.

### Step A — Twilio Account

- [X] Go to **console.twilio.com** → sign up or log in
- [X] Upgrade to a paid account (required for production WhatsApp)
- [X] Note your **Account SID** and **Auth Token** from the dashboard home — you'll need these for Vercel

### Step B — Meta Business Account

- [ ] Go to **business.facebook.com** → create a Meta Business Account if you don't have one
- [ ] Verify your business identity (may require documents — business name, address, website)

### Step C — WhatsApp Business API via Twilio

- [ ] In Twilio Console: **Messaging → Senders → WhatsApp Senders → Get Started**
- [ ] Connect your Meta Business Account to Twilio via the guided flow
- [ ] Choose or purchase a phone number for WhatsApp
- [ ] Submit your business profile for WhatsApp approval (Meta reviews it — typically 1–5 days)
- [ ] Wait for approval email

### Step D — Add Twilio credentials to Vercel

Once approved, go to **Vercel → Project Settings → Environment Variables** and add/update:

| Variable                      | Value                     | Where to find it                     |
| ----------------------------- | ------------------------- | ------------------------------------ |
| `TWILIO_ACCOUNT_SID`        | `ACxxxxxxxxxxxxxxxx`    | Twilio dashboard home                |
| `TWILIO_AUTH_TOKEN`         | `xxxxxxxxxxxxxxxx`      | Twilio dashboard home                |
| `TWILIO_FROM`               | `whatsapp:+1xxxxxxxxxx` | Your approved Twilio WhatsApp number |
| `TWILIO_VALIDATE_SIGNATURE` | `true`                  | (type it)                            |

Then **Redeploy** on Vercel.

### Step E — Wire the webhook

- [ ] Copy your live Vercel URL (e.g. `https://nudge-xyz.vercel.app`)
- [ ] In Twilio Console: **Phone Numbers → Manage → Active Numbers → your number**
- [ ] Under Messaging, set **"When a message comes in"** → Webhook → `https://nudge-xyz.vercel.app/twilio` → HTTP POST
- [ ] Save

---

## 2. Verify Everything Is Working

- [ ] `https://nudge-xyz.vercel.app/health` → JSON with `"status": "ok"` and `mfi_districts` > 0
- [ ] `https://nudge-xyz.vercel.app/` → landing page loads correctly
- [ ] `https://nudge-xyz.vercel.app/chat` → web chat works (type `START`, set a district, describe a loan)
- [ ] WhatsApp: send `START` to your Twilio number → bot replies
- [ ] WhatsApp: reply with a district name → bot confirms district set
- [ ] WhatsApp: send a loan description (e.g. "Need 5000 for 30 days at 5% monthly") → bot gives alternatives

---

## 3. Admin Endpoint

One admin endpoint is protected by `X-Admin-Token` (set `NUDGE_ADMIN_TOKEN` in Vercel env vars):

```bash
# Download anonymised metrics CSV for research
curl "https://nudge-xyz.vercel.app/admin/export-metrics" \
  -H "X-Admin-Token: your-admin-token" \
  -o metrics.zip
```

---

## Already Done

- [X] Supabase project created, `DATABASE_URL` set in Vercel
- [X] Anthropic API key set in Vercel (`CLAUDE_API_KEY`)
- [X] Vercel project deployed with env vars
- [X] PostgreSQL schema auto-created on first boot
