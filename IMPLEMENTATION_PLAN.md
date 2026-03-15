# IMPLEMENTATION_PLAN.md

# CASTLINE implementation plan — source of truth

_Last updated: 2026-03-15 04:31 America/Edmonton_

## Build posture

- **Ship fast.** Any older wording implying a leisurely multi-week pace is stale unless blocked by a real dependency.
- **iPhone-first.** Prioritize the mobile experience on iOS before broad Android/generalization work.
- **Thin vertical slices beat broad scaffolding.** Prefer end-to-end slices that prove the product loop.
- **Architecture doc informs this plan.** When there is conflict, this file wins for sequencing and current priorities.

## Current reality

- Present in workspace:
  - `castline-architecture.md`
  - `OPENCLAW_PROGRESS.md`
- Missing:
  - actual CASTLINE application/code repository
  - backend/mobile/infrastructure directories described in the architecture spec
- Therefore the immediate job is **bootstrap the real project repo and first working vertical slice**, not more abstract planning.

## Primary objective

Get to a working iPhone-first MVP slice as quickly as possible:

1. save a fishing spot
2. fetch live conditions for that spot from one real source (USGS first)
3. render an opinionated mobile conditions screen
4. generate at least one actionable alert/event from real data

If we can do those four things cleanly, CASTLINE becomes real.

## Phase ordering (accelerated)

### Phase A — Repo bootstrap and product skeleton
**Status:** active now
**Goal:** create the actual project structure so execution can start immediately.

Deliverables:
- monorepo/folder structure matching the architecture at a pragmatic level
- `README.md`
- backend app skeleton (FastAPI)
- mobile app skeleton (React Native / Expo if fastest path for iPhone-first delivery)
- shared environment/config conventions
- initial Docker Compose / local dev story only as needed

Exit criteria:
- project installs locally
- backend boots
- mobile app boots in iPhone simulator/device

### Phase B — Real conditions vertical slice
**Status:** next
**Goal:** prove real value with one spot and one datasource.

Deliverables:
- spot model + CRUD (minimal)
- USGS gauge lookup / spot association
- fetch current water temperature, discharge, gage height
- backend endpoint returning normalized current conditions
- iPhone spot detail screen showing those conditions with trends/placeholders

Exit criteria:
- user can view a saved spot with live USGS-backed conditions in app

### Phase C — First alert engine
**Status:** queued
**Goal:** turn data into action.

Deliverables:
- basic derived features from recent USGS observations
- first rule-based alerts, e.g.:
  - flow entering fishable band
  - temperature entering species-friendly range
  - rapid rise/fall detection
- alert feed in app
- local/in-app notifications first; push plumbing after logic is real

Exit criteria:
- at least one real alert can be generated and displayed from live data

### Phase D — Forecast/prediction substrate
**Status:** queued
**Goal:** build the minimum predictive path without waiting on full ML stack.

Deliverables:
- simple trend/extrapolation baseline for 6-24h outlook
- timeline UI for upcoming condition changes
- interfaces designed so TFT/advanced models can replace heuristics later

Exit criteria:
- app shows upcoming condition changes with clear caveats and explanations

### Phase E — Account/persistence/polish
**Status:** queued
**Goal:** make it usable beyond a demo.

Deliverables:
- auth
- durable spot persistence
- settings
- notification preferences
- basic analytics/logging/error handling

## Priority stack right now

1. **Create the missing codebase/skeleton**
2. **Implement USGS-backed conditions path**
3. **Build iPhone-first UI for spot + conditions**
4. **Add first event alerts**
5. **Only then expand to NOAA/tides/radar/ML complexity**

## Explicit deprioritization for now

Do **not** let these delay the first slice:
- full multi-source ingestion architecture
- GNN watershed graph build
- TFT training pipeline
- Android parity
- fish photo ID
- production-grade infra beyond what is needed to run locally
- lengthy validation paperwork before basic product existence

These remain important, but only after the first real user loop works.

## Working assumptions

- If the fastest route to an iPhone-first build is Expo/React Native, use it.
- If the fastest route to demonstrate conditions is direct backend polling/caching of USGS, do that before standing up a full queue/stream system.
- Favor clean interfaces so the eventual advanced architecture can slot in without a rewrite.

## Coordination rules

- Use sub-agents aggressively for independent slices once the codebase exists.
- Before a sub-agent closes, append a dated handoff to `OPENCLAW_PROGRESS.md`.
- When a sub-agent closes and a new feature begins, send a concise Telegram transition update to `8459145610` on the configured Telegram channel.
- Update this file whenever priorities or sequencing change.

## Immediate next action

**Bootstrap the CASTLINE repository in this workspace and implement the first end-to-end iPhone-first conditions slice.**
