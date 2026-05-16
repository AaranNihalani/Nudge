# NudgeAI (WhatsApp RL Nudge Optimisation) Spec

## Why
Informal credit borrowers in India often take high-interest loans due to lack of timely information about regulated alternatives. A single well-timed, low-frequency WhatsApp nudge can redirect borrowers to cheaper, RBI-regulated options without requiring a new app or complex onboarding.

## What Changes
- Build a public WhatsApp bot that can (a) detect when a user is likely to borrow from a high-cost lender and (b) send a single, context-relevant nudge pointing to regulated alternatives in the user’s district.
- Maintain a curated MFI database (RBI-regulated lenders, rates, districts, contact/links) in SQLite and expose read/query capability to the bot and policy.
- Parse Hindi-English WhatsApp messages into structured “borrowing intent + terms” events using the Claude API (no fine-tuning).
- Implement an RL formulation for nudge timing and content selection:
  - State: time since last borrow, implied interest rate, debt burden proxy, nudge history, engagement history, district, lender type
  - Action: wait / alert / suggest lender / send education
  - Reward: engagement, lender-switch proxy/confirmation, penalty for spam and user opt-outs
- Train PPO on synthetic trajectories calibrated to AIDIS-like distributions and compare against a simple baseline (threshold/heuristic).
- Deploy the bot service (Twilio WhatsApp webhook → Flask API) to Railway with a small, auditable footprint.
- **BREAKING**: None (new system).

## Impact
- Affected specs: onboarding/consent, message parsing, event storage, MFI discovery, nudge policy runtime, RL training + evaluation, deployment + ops.
- Affected code: new Flask service, SQLite schema/migrations, policy engine, RL environment + training scripts, evaluation harness, minimal tests.

## ADDED Requirements

### Requirement: User Consent & Safety
The system SHALL require explicit opt-in before processing user messages for nudge optimisation.

#### Scenario: Opt-in
- **WHEN** a user sends “START” (or an equivalent opt-in keyword)
- **THEN** the system records consent and sends a short explanation of data use and how to opt out

#### Scenario: Opt-out
- **WHEN** a user sends “STOP”
- **THEN** the system disables nudges, stops policy actions for that user, and confirms opt-out

### Requirement: WhatsApp Webhook Processing
The system SHALL accept inbound WhatsApp messages via Twilio webhook and persist them as user events.

#### Scenario: Inbound message stored
- **WHEN** Twilio posts an inbound message payload
- **THEN** the system stores a normalized event with timestamp, sender, raw text, and channel metadata in SQLite

### Requirement: NLP Parsing to Borrowing Events
The system SHALL transform raw message text into structured borrowing-related events using the Claude API.

#### Scenario: Borrow intent detected
- **WHEN** a message indicates intent to borrow or active negotiation (e.g., amount, tenure, interest, lender)
- **THEN** the system records a structured event (intent=true) including extracted fields (amount/tenure/rate/lender type) with confidence

#### Scenario: No borrow signal
- **WHEN** a message is unrelated to borrowing
- **THEN** the system records a parsed event with intent=false and does not update “borrow timing” state variables

### Requirement: MFI Database & District Lookup
The system SHALL maintain a local database of regulated lenders and return district-matched alternatives and their indicative rates.

#### Scenario: Query regulated alternatives
- **WHEN** the system needs to suggest an alternative in a user’s district
- **THEN** it returns a ranked list of regulated options for that district with rate range and contact/links

### Requirement: Nudge Policy Runtime
The system SHALL compute a user state from stored events and select exactly one of the allowed actions at each decision point.

#### Scenario: Decision point
- **WHEN** a new borrowing-related event is recorded or a scheduled daily check runs
- **THEN** the system computes the current state and selects an action from {wait, alert, suggest_lender, education}

#### Scenario: Spam prevention
- **WHEN** the user has been nudged within a configurable cooldown window
- **THEN** the policy MUST choose wait unless an explicit high-risk/urgent condition is met

### Requirement: Message Generation & Sending
The system SHALL generate a short, understandable WhatsApp message aligned to the chosen action and send it via Twilio.

#### Scenario: Suggest lender
- **WHEN** the action is suggest_lender
- **THEN** the system sends one message containing (a) a simple comparison framing and (b) 1–3 regulated alternatives in the user’s district

### Requirement: RL Environment & Synthetic Data
The system SHALL provide a training environment that simulates user trajectories and nudge outcomes calibrated to survey-like distributions.

#### Scenario: Generate trajectories
- **WHEN** training is started
- **THEN** the system generates synthetic user trajectories (target: 10,000) using calibrated parameters (borrow frequency, rate distributions, engagement propensity)

### Requirement: PPO Training & Baseline Comparison
The system SHALL train a PPO policy using Stable-Baselines3 and evaluate it against a naive baseline.

#### Scenario: Train and evaluate
- **WHEN** training completes
- **THEN** the system outputs evaluation metrics for PPO and baseline (e.g., engagement rate, nudge count per user, switch proxy) and supports ablations on reward shaping weights

### Requirement: Observability & Auditability
The system SHALL provide basic operational visibility without storing secrets in logs.

#### Scenario: Trace a user decision
- **WHEN** investigating a decision
- **THEN** the system can show which state features and policy action were used (in internal logs or admin output) while avoiding raw secrets and minimizing sensitive data exposure

## MODIFIED Requirements
None (new system).

## REMOVED Requirements
None.

