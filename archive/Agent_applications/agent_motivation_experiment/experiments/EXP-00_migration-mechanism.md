# EXP-00 — Migration mechanism verification (no code change)

**Status**: in progress · **Date**: 2026-07-06

## Why
Before any throughput/goodput study, confirm that Llumnix's live **request
migration** actually works end-to-end on this deployment, without modifying the
`Agent_applications` code. This is a functional/mechanism check, not a
measurement.

## What we want to see
The full migration pipeline firing: **load imbalance detected → rescheduling
pair generated → `Migrate` gRPC delivered to the source engine → KV cache
actually transferred to a destination engine** (destination KV rises).

## Background: the catch-22 (established from code + docs)
- **Dispatch** (`--scheduling-policy load-balance`) routes each new request to the
  least-loaded instance (argmin of `all_prefills_tokens_num`), keeping the 4
  engines balanced. **Migration** (`neutral_load`) only fires when two engines'
  `kv_cache_usage_ratio_projected` differ by ≥ `load-balance-threshold` (0.1 =
  10%p), with a source ≥ `neutral-load-threshold` (0.003) and an idle destination.
- So under normal gateway load, dispatch prevents the very imbalance migration
  needs → migration rarely triggers.
- A request is only **migratable** if it was dispatched through the gateway/
  scheduler (Llumnix-managed). Directly-injected requests are not.

## Runs & observations

### Run A — λ=50 Poisson through the gateway (load-balance)
- Setup: in-cluster runner, `sharegpt`, `poisson-sweep λ=50`, 3 min, RESTART=0.
  Run dir: `results/260706_0126_mig_check_lambda_50/`.
- Result: **no migration**. Across 388 ticks, max cross-engine KV gap = **1.39%p**
  (need ≥10%p); peak KV only ~2% (Llama-3-8B barely fills KV on B200). 0/388 ticks
  hit the threshold. Confirms: gateway dispatch keeps engines balanced (also aided
  by `EnableInstanceStatusLocalAccount=true` — the scheduler tracks what it just
  dispatched, so bursts don't pile onto one instance).
- **Conclusion**: gateway load cannot create the imbalance migration needs here.

### Run B — direct injection to one engine (:8000 only)
- Setup: 80 concurrent long (`ignore_eos`, 4000-token) completions injected
  **directly** to engine `neutral-0` port 8000 (bypassing the gateway), 120s.
- Result: engine 8000 KV climbed to **9–20%**, engines 8001/8002/8003 stayed at
  **0%** → gap > 10%p. The scheduler **detected it and generated migration pairs**:
  `Generate rescheduling pairs, count: 1` (118×), and delivered the gRPC:
  ```
  rpc_server.py:252  Received Migration request.
    MigrationParams(migration_type='TOKEN', mig_req_policy='SR', num_reqs=1, num_tokens=1024)
  ```
  (the default migration params, exactly). **But no KV transferred** — engines
  8001-3 stayed at 0%, and the source engine logged:
  ```
  kvt_migration_frontend.py:254  WARNING  No requests to migrate, migration type: TOKEN
  ```
- **Conclusion**: the entire migration **control path works** (detect → decide →
  gRPC → engine handler), but the directly-injected requests are **not
  Llumnix-managed**, so the source has "no requests to migrate". This is the
  catch-22, now proven with logs.

### Run C — flood dispatch (force imbalance among *migratable* requests) ← current
- **Idea**: change dispatch to `--scheduling-policy flood`, which sends **all
  gateway requests to one fixed instance** (`fixedPreferenceSelector`). Those are
  gateway-dispatched → **migratable**. That instance's KV climbs while the others
  stay idle → `neutral_load` migrates real requests to them → **destination KV
  rises = actual KV transfer**.
- Config change (no code): scheduler `--scheduling-policy load-balance → flood`
  (applied via edited live manifest; reverted after). `round-robin` was rejected
  as an option because it balances by count and wouldn't create imbalance.
- Load: long concurrent completions to **the gateway** (`gateway:8089`), so they
  are Llumnix-managed.
- **Expected**: one engine hot; engines that receive **no dispatch** (flood targets
  only one) nonetheless show **rising KV** — that can only come from migration.
  Engine logs show `Received Migration request` WITHOUT "No requests to migrate";
  `scheduler_rescheduling_total{rule=TOKEN,order=SR}` increments.
- **Observed**: flood alone did **not** trigger migration — scheduler generated
  `count: 0` pairs (401×) throughout, no engine migration logs. Reason: through
  the gateway I could not force long outputs (`ignore_eos` isn't honored on the
  gateway path), so the flood-target instance's outputs stayed short → KV drained
  fast → the instance never held enough KV to be a source (need ≥0.3% with a ≥10%p
  gap). On this fleet (Llama-3-8B on B200) KV occupancy is tiny unless requests
  hold long sequences. **Flood is insufficient without long-held KV.**
- Config reverted to `load-balance` afterward.

## Conclusion (EXP-00)
The migration **mechanism is verified functional**: Run B showed the complete
control path (imbalance detection → pair generation → `Migrate` gRPC to the engine
with the correct default params TOKEN/SR/1024). A **completed byte-level KV
transfer** was not observed because, on this over-provisioned fleet, producing a
≥10%p KV-usage imbalance **among Llumnix-managed (gateway-dispatched) requests**
requires long-held KV that the current no-code load paths don't generate
(gateway strips `ignore_eos`; direct injection isn't migratable). Observing a real
transfer would need either a workload with long sustained sequences dispatched via
the gateway, or a smaller KV budget (lower `--gpu-memory-utilization` / smaller
`--max-model-len`) so modest load fills KV. Deferred — not a blocker for the
throughput/goodput study, which uses the reference `load-balance` config.

## Findings so far
- Migration **control plane is fully functional** (Runs A/B): detection, pair
  generation, gRPC delivery to the engine with correct default params
  (TOKEN / SR / 1024 tokens).
- A real byte-level KV transfer needs **imbalance among gateway-dispatched
  (migratable) requests**, which `load-balance` dispatch prevents by design. Run C
  (flood) is the no-code way to produce it.
