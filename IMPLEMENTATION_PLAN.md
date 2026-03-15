# IMPLEMENTATION_PLAN.md

# CASTLINE implementation plan — source of truth

_Last updated: 2026-03-15 06:15 America/Edmonton_

## Build posture

- **Ship fast.** Any older wording implying a leisurely multi-week pace is stale unless blocked by a real dependency.
- **iPhone-first.** Prioritize the mobile experience on iOS before broad Android/generalization work.
- **Thin vertical slices beat broad scaffolding.** Prefer end-to-end slices that prove the product loop.
- **Architecture doc informs this plan.** When there is conflict, this file wins for sequencing and current priorities.
- **Validate the thesis first.** The architecture explicitly says to begin with Phase 0 validation before major engineering investment.

## Current reality

- Present in workspace:
  - `castline-architecture.md`
  - `OPENCLAW_PROGRESS.md`
  - thin `mobile/` shell work from the parallel iPhone lane
  - early `castline/validation/` and `castline/backend/` repository skeletons
- Still missing or incomplete:
  - real ingestion collectors and historical scrapers
  - populated validation datasets beyond samples/templates
  - executable backend product endpoints
- Therefore the immediate job is **turn the validation skeleton into a working Phase 0 pipeline**, while keeping app scaffolding intentionally thin and secondary.

## Primary objective

Reduce the biggest product risk as quickly as possible:

1. prove that the environmental-feature thesis materially outperforms a simpler baseline
2. build the minimum validation-oriented codebase needed to execute that work
3. only in parallel where useful, keep a thin iPhone shell available for demos and eventual productization

## Validation objective

Phase 0 from the architecture should lead:

1. collect historical outcome data (tournament/creel or closest available proxy)
2. collect historical environmental data with USGS first
3. build a reproducible validation dataset
4. compare a baseline model vs a richer environmental-feature model
5. decide whether the thesis is weak, viable, or strong

## Validation decision rubric

- The validation lane should produce an explicit judgment, not just artifacts.
- Use the architecture's decision logic:
  - if the richer environmental-feature model improves performance by less than 5% over baseline, treat the thesis as weak
  - if improvement is roughly 5-15%, treat the thesis as viable but requiring caution
  - if improvement is greater than 15%, treat the thesis as strong and proceed aggressively
- Record the judgment, evidence, and caveats in:
  - `IMPLEMENTATION_PLAN.md`
  - `OPENCLAW_PROGRESS.md`
  - a dedicated validation summary file once the repo scaffold exists
- If the result is weak, the next step is not "blindly continue building"; the next step is to reconsider scope, signal source quality, or modeling assumptions.

## ML research policy

- For every ML technique named in the architecture, research from primary sources before implementation.
- Prefer this order:
  1. original paper / canonical publication
  2. official or author-linked code
  3. high-quality reference implementations
  4. Hugging Face models or community repos only when they are clearly appropriate
- Techniques mentioned in the architecture that should be researched deliberately include:
  - Temporal Fusion Transformer (TFT)
  - graph-based watershed or upstream propagation approaches where relevant
  - simpler baselines such as logistic regression, gradient boosting, and time-series heuristics
- Do not jump straight to the heaviest model. Start with reproducible baselines and only escalate complexity when validation justifies it.

## GPU policy

- A GPU is not required for the earliest Phase 0 tasks:
  - data collection
  - dataset assembly
  - feature engineering
  - baseline modeling
- Delay GPU-dependent training until:
  - the validation dataset exists
  - baseline comparisons are running
  - there is a concrete reason to train a heavier model such as TFT
- If a GPU becomes useful, prefer a short-lived rented environment instead of designing around permanent GPU availability.
- Treat Vast.ai as optional acceleration, not a prerequisite for starting.

## Secondary product objective

After the validation lane is materially underway, continue toward a working iPhone-first MVP slice:

1. save a fishing spot
2. fetch live conditions for that spot from one real source (USGS first)
3. render an opinionated mobile conditions screen
4. generate at least one actionable alert/event from real data

If we can do those four things cleanly, CASTLINE becomes real.

## Phase ordering (accelerated)

### Phase 0 — Validation
**Status:** active now
**Goal:** de-risk the thesis before broad product build-out.

Current sub-status:
- repository skeleton exists
- baseline-vs-environment comparison harness now exists in `castline/validation/`
- manifest-driven USGS collection and weather joins are wired into the dataset path
- official Bassmaster result-page scraping now exists for building tournament-day outcomes from standings PDFs
- first-pass Bassmaster-to-USGS mapping suggestion tooling now exists to accelerate gauge curation
- direct IEM ASOS weather collection now exists from the outcomes manifest, including state-network station selection and per-event weather summaries
- next gap is turning those mapping suggestions into curated mapped real tournament rows at useful scale, then running the first real-data comparison judgment

Deliverables:
- validation-first repository structure
- historical outcome ingestion/scraping scripts
- historical USGS/environmental data pull scripts
- reproducible validation dataset assembly
- baseline-vs-full-feature comparison workflow
- written result showing whether the thesis is weak, viable, or strong

Exit criteria:
- a first validation run exists and produces interpretable results
- the team can say whether deeper ML/product investment is justified

### Phase A — Thin product shell
**Status:** secondary / parallel only when helpful
**Goal:** keep a lightweight iPhone-facing shell available without overtaking validation.

Deliverables:
- minimal repo/app/backend structure
- mobile shell for demos/testing
- only enough backend surface to support real-source experiments and later productization

Exit criteria:
- the shell exists, but it does not distract from validation priorities

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

1. **Create the missing validation-oriented codebase/skeleton**
2. **Start historical outcome + USGS data collection**
3. **Build the first baseline-vs-full-feature validation workflow**
4. **Only secondarily scaffold the iPhone shell**
5. **Then implement USGS-backed conditions, alerts, and broader product loops**

## Explicit deprioritization for now

Do **not** let these delay the first slice:
- polished app build-out before validation has started
- full multi-source ingestion architecture
- GNN watershed graph build
- TFT training pipeline before baseline validation exists
- Android parity
- fish photo ID
- production-grade infra beyond what is needed to run locally

These remain important, but only after the thesis is being validated and the first product loop is justified.

## Working assumptions

- If the fastest route to Phase 0 validation is Python-first scripts plus minimal backend structure, do that.
- If the fastest route to an eventual iPhone-first build is Expo/React Native, use it for the secondary shell lane.
- If the fastest route to demonstrate conditions is direct backend polling/caching of USGS, do that before standing up a full queue/stream system.
- Favor clean interfaces so the eventual advanced architecture can slot in without a rewrite.
- Prefer CPU-friendly baselines first; use GPU only when heavier training is truly warranted.

## Coordination rules

- Use sub-agents aggressively for independent slices once the codebase exists.
- Before a sub-agent closes, append a dated handoff to `OPENCLAW_PROGRESS.md`.
- When a sub-agent closes and a new feature begins, send a concise Telegram transition update to `8459145610` on the configured Telegram channel.
- Update this file whenever priorities or sequencing change.

## Immediate next action

**Feed the manifest-driven validation pipeline a first real batch of historical tournament/creel outcomes with curated `usgs_site_id` mappings; the weather lane can now be fetched directly from IEM once those mapped rows exist.**
