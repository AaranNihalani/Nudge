# Nudge — Remaining Setup Checklist

WhatsApp has been removed. Nudge is a web chatbot only, available at `/chat`.

---

## 1. Verify the Deployment

- [ ] `https://your-project.vercel.app/health` → JSON with `"status": "ok"` and `mfi_districts` > 0
- [ ] `https://your-project.vercel.app/` → landing page loads
- [ ] `https://your-project.vercel.app/chat` → chat UI works (type `START`, set a district, describe a loan)

---

## 2. Environment Variables (Vercel)

Go to **Vercel → Project Settings → Environment Variables** and confirm these are set:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `postgresql://postgres:...` (Supabase URI) |
| `CLAUDE_API_KEY` | `sk-ant-...` |
| `NUDGE_MFI_AUTOLOAD` | `true` |
| `NUDGE_ADMIN_TOKEN` | any strong secret |

Redeploy after any changes.

---

## 3. Admin Endpoint

```bash
curl "https://your-project.vercel.app/admin/export-metrics" \
  -H "X-Admin-Token: your-admin-token" \
  -o metrics.zip
```

---

## Already Done

- [X] Supabase project created, `DATABASE_URL` set in Vercel
- [X] Anthropic API key set in Vercel (`CLAUDE_API_KEY`)
- [X] Vercel project deployed
- [X] PostgreSQL schema auto-created on first boot
