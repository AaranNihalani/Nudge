# Tasks
- [x] Task 1: Establish project skeleton and runtime configuration
  - [x] Create a minimal Flask API service structure for Twilio webhooks
  - [x] Define configuration loading for Twilio/Claude keys (env-based) and Railway deployment defaults
  - [x] Add a minimal local run path and a health endpoint

- [x] Task 2: Implement SQLite storage for events, users, and MFI data
  - [x] Define SQLite schema for: users (consent, district), raw messages, parsed events, nudges sent, lender switches (self-reported)
  - [x] Add simple migration/initialization logic for local + Railway environments

- [x] Task 3: Build MFI database ingestion and query layer
  - [x] Create a structured dataset format (CSV/JSON) for lenders, districts, and indicative rates
  - [x] Implement loader to populate SQLite
  - [x] Implement queries: by district, by rate range, top-N alternatives with tie-breaking

- [x] Task 4: Implement consent, onboarding, and safe defaults
  - [x] Handle START/STOP and store consent state
  - [x] Implement district capture flow (prompt and update) with fallback if unknown
  - [x] Enforce cooldown and message frequency caps

- [x] Task 5: Implement Claude-based NLP parsing pipeline
  - [x] Define a constrained JSON output schema for parsing borrow intent + terms
  - [x] Implement Claude call wrapper with retries/timeouts and strict JSON validation
  - [x] Persist parsed events with confidence and extracted fields

- [x] Task 6: Implement state computation and baseline policy
  - [x] Compute state features from stored events (days since borrow, implied rate, debt burden proxy, nudge history, engagement)
  - [x] Implement a naive threshold baseline policy for timing and content selection
  - [x] Wire baseline policy into the live bot runtime behind a feature flag

- [x] Task 7: Create RL environment and synthetic trajectory generator
  - [x] Define environment dynamics calibrated to AIDIS-like distributions (borrow frequency, rate distributions, engagement propensity)
  - [x] Implement a trajectory generator (target 10,000 users) and dataset export
  - [x] Add unit-level checks to ensure distributions are within expected ranges

- [x] Task 8: Train PPO policy and add evaluation + ablations
  - [x] Implement PPO training using Stable-Baselines3 with CPU-friendly settings
  - [x] Implement evaluation harness comparing PPO vs baseline on the same synthetic set
  - [x] Implement reward shaping ablations (configurable weights) and report metrics

- [x] Task 9: Productionize policy serving for pilot
  - [x] Add model loading/versioning and safe fallback to baseline
  - [x] Add scheduled decision points (daily check) in addition to event-triggered decisions
  - [x] Add minimal admin tooling to export anonymized metrics for the paper

- [x] Task 10: Validation and deployment readiness
  - [x] Add end-to-end local test path using recorded webhook payload fixtures
  - [x] Add smoke checks for: consent, parsing, MFI query, and sending a nudge
  - [x] Ensure Railway deploy runs migrations/initializes DB safely on startup

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 1 and Task 2
- Task 5 depends on Task 1 and Task 2
- Task 6 depends on Task 2 and Task 5
- Task 7 depends on Task 6
- Task 8 depends on Task 7
- Task 9 depends on Task 6 and Task 8
- Task 10 depends on Tasks 1–9
