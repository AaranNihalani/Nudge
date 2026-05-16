# Twilio WhatsApp Manual Test Scripts

This file lists WhatsApp messages to send (user → bot) and the expected responses, grouped by feature.

Unless noted otherwise, keywords are case-insensitive and must match exactly (e.g., `MORE`, `START`, `STOP`).

## Assumptions / Setup

- The bot is running and reachable by Twilio’s WhatsApp webhook.
- Policy mode is enabled (baseline) so loan messages are parsed and can trigger suggestions/alerts.
- For deterministic “alternatives” text in examples, seed the DB with:
  - District `D`
  - Lender `A` at `24% APR` in district `D`

## Consent / Help

### Not opted in → prompt to START

1) User:

```
hello
```

Expected bot response:

```
To get nudges, reply START to opt in. Reply STOP to opt out.
```

### START (no district set yet)

1) User:

```
START
```

Expected bot response (exact text, except the example district names depend on the DB):

```
You’re opted in. NudgeAI is on.

To personalise suggestions, reply with your district name.
Examples: <district1>, <district2>, ...
You can also type:
- DISTRICTS (to see more)
- MORE
- DISTRICT <name>

Language:
- LANG EN / LANG HI / LANG HINGLISH

Reply STOP anytime to opt out.
```

### START (district already set)

Precondition: user has a district set.

1) User:

```
START
```

Expected bot response (exact text):

```
You’re opted in. NudgeAI is on.
District: <your_district>

How to use:
1) Tell me what loan you’re about to take (amount + time + interest).
2) I’ll suggest regulated lenders in your district if it looks expensive.

Commands:
- DISTRICTS (or DISTRICTS <prefix>)
- MORE
- DISTRICT <name>
- LANG EN / LANG HI / LANG HINGLISH
- CORRECT <field>=<value>
- HELP
- STOP

Example message:
“Need 5000 for 30 days. Moneylender says 5% monthly.”
```

### HELP

1) User:

```
HELP
```

Expected bot response (exact text):

```
NudgeAI help

What I do:
- If you’re about to take a high-interest loan, I point you to cheaper regulated alternatives in your district.
- I keep messages low-frequency to avoid spam.

Commands:
- START
- STOP
- DISTRICT <name>
- DISTRICTS (or DISTRICTS <prefix>)

- MORE (to keep listing districts)

- CORRECT <field>=<value>

- CONTACTED <lender>

- SWITCHED <lender> (or SWITCHED FROM <old> TO <new>)

- LANG EN / LANG HI / LANG HINGLISH

To get suggestions, send a message like:
“Need 5000 for 30 days. Interest 5% monthly.”
```

### STOP

1) User:

```
STOP
```

Expected bot response (exact text):

```
You’re opted out. Reply START anytime to opt back in.
```

## District Setting

### Set district (explicit command)

1) User:

```
DISTRICT D
```

Expected bot response (exact text):

```
district set to D

Now send your loan terms and I’ll suggest regulated alternatives if it looks expensive.
Example: “Need 5000 for 30 days. Interest 5% monthly.”
```

### Set district (implicit: user just sends a district name)

Precondition: user is opted in and has no district set.

1) User:

```
D
```

Expected bot response: same as the explicit command (`district set to D ...`).

### District name doesn’t match DB list (when the DB has districts loaded)

Precondition: `mfi_districts` is populated.

1) User:

```
DISTRICT NotARealPlace
```

Expected bot response (current implementation accepts unknown districts and stores the raw text):

```
district set to NotARealPlace

Now send your loan terms and I’ll suggest regulated alternatives if it looks expensive.
Example: “Need 5000 for 30 days. Interest 5% monthly.”
```

## District Listing + Paging

### List districts (first page)

Precondition: there are at least 65 districts in `mfi_districts` sorted lexicographically, e.g. `D000..D064`.

1) User:

```
DISTRICTS
```

Expected bot response:

- Includes:
  - `Districts (showing 30 of 65):`
  - A comma-separated list containing `D000` and `D029`
  - `Reply: DISTRICT <name>`
  - `Reply MORE for more.`
- Does not include `D030` on the first page.

### MORE (next page)

Precondition: you just ran `DISTRICTS` and got a response that says “Reply MORE for more.”

1) User:

```
MORE
```

Expected bot response:

- Starts with `Districts (showing 60 of 65):` (exact count depends on total)
- Includes `D030` on this page.

### MORE without listing first

1) User:

```
MORE
```

Expected bot response (exact text):

```
Reply DISTRICTS to list districts first.
```

### MORE when there are no more pages

Precondition: keep replying `MORE` until the last page is shown.

1) User:

```
MORE
```

Expected bot response (exact text):

```
No more districts. Reply DISTRICTS to start again.
```

### Prefix filter

1) User:

```
DISTRICTS Kam
```

Expected bot response:

- Starts with `Districts for “Kam”`
- Lists only districts starting with `Kam` (case-insensitive).

### No matches

1) User:

```
DISTRICTS zznotaprefix
```

Expected bot response (exact text):

```
No matching districts found.
```

## Loan Parsing + Suggestion / Alert

### Full loan message (triggers alert when APR ≥ 60 and stage is asking/offered/agreed/borrowed)

Precondition:

- User is opted in
- District is set to `D`
- The DB has lender `A` at `24% APR` for district `D`
- The parsing result yields:
  - `amount_inr = 5000`
  - `tenure_days = 30`
  - `interest_rate_apr = 60.0`
  - `negotiation_stage = asking`

1) User:

```
Need 5000 for 30 days at 5% monthly
```

Expected bot response (exact structure, content depends on lender list; with the seeded DB it should include lender `A`):

```
If you’re being quoted ~60% APR (≈5%/month), that’s very costly.
Rough estimate for INR 5,000 over 30 days (no fees, simple interest): repay ~INR 5,247 (interest ~INR 247). At ~24% APR repay ~INR 5,099 (save ~INR 148).
In D, some regulated alternatives (APR is annualised):
1) A (~24% APR ≈2%/month)

Why regulated? They’re licensed/registered and overseen, and usually have clearer terms and fairer collections rules.

Reply DISTRICT <name> to change district. Reply STOP to opt out.
```

### Lower “costly but not extreme” message (triggers suggestions when 40 ≤ APR < 60)

Precondition:

- Same as above, but implied APR is `48%` (e.g., `4% monthly`) and stage is `asking` (or `considering`, `offered`, `agreed`).

1) User:

```
Need 5000 for 30 days at 4% monthly
```

Expected bot response:

- Starts with `Your quoted rate: ~48% APR (≈4%/month).`
- Includes a `Rough estimate for INR ...` line
- Includes `In D, some regulated alternatives with lower indicative APR (APR is annualised):`
- Includes the numbered lender list.

### Not a borrow-intent message (policy will “wait”)

Precondition:

- User is opted in and district is set.
- No borrow-intent has been recorded in the last 14 days (or there has never been one).

1) User:

```
thanks
```

Expected bot response (exact text):

```
Thanks — I’ve got your message. If you’re discussing a loan, share the rate/tenure and I can suggest regulated options. Reply STOP to opt out.
```

## Clarifications (Missing Fields → Draft → Resume)

### Missing interest rate → ask a clarifying question → next user message resumes the draft

Precondition: user is opted in and district is set.

1) User:

```
Need 5000 for 30 days
```

Expected bot response (exact text):

```
What interest rate did they quote? Example: 5% monthly (or 60% APR)
```

2) User:

```
5% monthly
```

Expected bot response:

- Should now behave like the “Full loan message” case and include `very costly` when implied APR reaches `60%`.

### Missing amount

1) User:

```
Need a loan for 30 days at 5% monthly
```

Expected bot response (exact text):

```
How much do you want to borrow (in INR)? Example: 5000
```

### Missing tenure

1) User:

```
Need 5000 at 5% monthly
```

Expected bot response (exact text):

```
How long is the loan for? Example: 30 days (or 2 months)
```

## Corrections

### Correct an already-parsed event (no active draft)

Precondition:

- A borrow-intent event was previously parsed and saved for this user.

1) User:

```
CORRECT rate=2% monthly
```

Expected bot response:

- Starts with `Updated.`
- If the corrected APR drops below the nudge thresholds, the remainder is typically:

```
Updated. Thanks — I’ve got your message. I’ll keep nudges low-frequency. Reply STOP to opt out.
```

### Correct while a clarification draft is active

Precondition:

- You previously received a clarifying question (so the session has a `borrow_draft`).

1) User:

```
CORRECT rate=5% monthly
```

Expected bot response:

- If that completes the missing field set, it should proceed to suggestion/alert as if you had sent the missing value normally.

### Unsupported / malformed correction

1) User:

```
CORRECT something_we_dont_support=123
```

Expected bot response (exact text):

```
Sorry — I couldn’t understand that correction. Example: CORRECT rate=5% monthly
```

### Correction with no prior loan details

Precondition: user has no recent saved borrow intent and no active draft.

1) User:

```
CORRECT rate=5% monthly
```

Expected bot response (exact text):

```
I don’t have any recent loan details to correct. Send your loan terms first.
```

## Verbose Replies (“Parsing Echo”)

Precondition: `NUDGE_VERBOSE_REPLIES=true` (or config `verbose_replies=True`).

1) User:

```
Need 5000 for 30 days at 5% monthly
```

Expected bot response:

- If the loan was parsed, the reply begins with a “Parsed loan:” block showing amount/tenure/APR and how to correct it.
- Then the normal human-facing message (alert/suggestion/wait)
- Then a blank line
- Then a status line beginning with `[status]` and containing:
  - `policy=...`
  - `parsed=yes|attempted|no`
  - `loan=amount=...,tenure_days=...,apr=...`
  - `limits=ok|blocked`

```
[status] policy=baseline | decision=alert | engine=baseline-threshold | parsed=yes | loan=amount=5000.0,tenure_days=30,apr=60.0 | limits=ok
```

## Low-Frequency Throttling (Cooldown / Caps)

Precondition: throttle settings are configured such that a second nudge would exceed cooldown or daily/weekly caps.

1) User:

```
Need 5000 for 30 days at 5% monthly
```

Expected bot response (exact text):

```
Thanks — I’ve got your message. I’ll send the next update later to keep messages low-frequency. Reply STOP anytime to opt out.
```

## Daily Runner (Scheduled Outbound)

This is not triggered by an inbound WhatsApp message. It is a scheduled/admin job that may send an outbound WhatsApp message.

Precondition:

- User is opted in and has a district set.
- The user has recent activity (inbound message or parsed event in the last 30 days).
- A policy decision results in a nudge (`alert`, `suggest_lender`, or `education`) and passes throttle checks.

Expected WhatsApp message (outbound to the user):

- Body equals the policy decision content (same format as “Suggestion / Alert”).
- The nudge is stored with `trigger='daily'` and `delivery_status`:
  - `queued` when Twilio credentials are not configured
  - otherwise populated from Twilio’s send result.

## Switch Tracking (CONTACTED / SWITCHED)

### CONTACTED

1) User:

```
CONTACTED ABC Microfinance
```

Expected bot response:

- Confirms it recorded the contact, for example:

```
Noted. contacted ABC Microfinance.
```

### SWITCHED

1) User:

```
SWITCHED ABC Microfinance
```

Expected bot response:

```
Noted. switched to ABC Microfinance.
```

### SWITCHED FROM ... TO ...

1) User:

```
SWITCHED FROM Moneylender TO ABC Microfinance
```

Expected bot response:

```
Noted. switched to ABC Microfinance.
```
