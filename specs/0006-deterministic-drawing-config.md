# Spec 0006 — Deterministic Drawing Config

- **Branch:** `spec/0006-deterministic-drawing-config` (from `main`, after 0005)
- **Status:** Implemented — merged to local `main`
- **Depends on:** 0005 (phases — different phases want different sampling).
- **North-star link:** Removes needless randomness from a precision task; makes
  results reproducible for evaluation.

---

## 1. Context

`agent._get_llm` hard-codes `temperature=0.7` for every provider and every step
(`agent.py:551`). Pixel placement is a precision task; 0.7 injects noise into
coordinates and color choices. There is also no seed, so nothing is
reproducible, which blocks the "10 fixed tasks" quality bars used in the other
specs.

## 2. Goal

Sampling parameters are centralized, low by default for drawing, tunable per
phase, and seeded where the provider supports it — so the same request produces
(near-)identical output and evaluation is meaningful.

## 3. Requirements

### R1 — Central config
- All sampling params (`temperature`, `top_p`, `seed`, provider extras) SHALL
  come from one config object, not literals scattered in `_get_llm`.

### R2 — Low default temperature
- Drawing/refine agent calls SHALL default to `temperature = 0.2`
  (env-overridable `AGENT_TEMPERATURE`).
- The concept-image generation call (`sprite.reference`) is unaffected.

### R3 — Per-phase override (uses 0005)
- Each phase MAY specify its own temperature: e.g. Silhouette/Cleanup `0.1`,
  Detail `0.35`. Defined in the phase table, falling back to R2.

### R4 — Seeding
- WHEN the provider supports a sampling seed (OpenAI `seed`, Gemini
  `generation_config.seed`, Ollama `options.seed`), THE SYSTEM SHALL pass a seed
  derived from `gen_id` unless the request overrides it.
- WHEN the provider does not support seeding, THE SYSTEM SHALL proceed and note
  it in logs.

### R5 — Request overrides
- `GenerateRequest` MAY carry `temperature` and `seed`; when present they win
  over defaults/phase values.

### R6 — Reproducibility check
- With a fixed `seed` + `temperature: 0` (where supported), two runs of the same
  reference-driven request SHALL produce identical `pixel_data` for the
  deterministic pipeline stages and ≥ 95% identical pixels for the agent stages.

## 4. Design sketch

- New `agent/_sampling.py` (or a dict in `agent.py`): `SamplingConfig` with
  resolution order request > phase > env > default.
- `_get_llm(model_name, sampling: SamplingConfig)` — build provider kwargs from
  it: `ChatOpenAI(temperature=..., model_kwargs={"seed": ...})`,
  `ChatGoogleGenerativeAI(temperature=..., ...)`,
  Ollama `extra_body={"options": {"seed": ..., "num_ctx": ...}}`.
- `run_agent_stream` accepts `sampling` (or `temperature`/`seed`) and threads it
  to `_get_llm`; phase orchestrator (0005) passes phase-specific values.
- Thread `temperature`/`seed` through `GenerateRequest` → server/worker → job
  handlers, same pattern as every prior spec.

## 5. Tasks

1. [x] `agent.SamplingConfig` (`temperature`, `seed`, `top_p`) +
   `resolve_sampling(request > phase > env)` + `_seed_from(gen_id)` (crc32).
2. [x] `_get_llm(model_name, sampling=None, temperature=None)` — one code path;
   `seed` wired to `ChatOpenAI`, `ChatGoogleGenerativeAI`, `ChatVertexAI`
   (all three expose a `seed` field), and to Ollama's `extra_body.options.seed`.
   `temperature=` kept as a back-compat shortcut (used by `scoring`).
3. [x] Default `AGENT_TEMPERATURE` = env `AGENT_TEMPERATURE` or `0.2`
   (was hard-coded `0.7`).
4. [x] `PHASE_TEMPERATURE` = silhouette/cleanup `0.10`, base_colors `0.15`,
   shading `0.30`, detail `0.35`.
5. [x] `GenerateRequest.temperature` / `.seed` → `_run_agent_sse` →
   `run_agent_stream` / `run_phased_generation` (per-phase call), redis payload,
   `worker.handle_generate`, `SpriteGenerateParams`.
6. [x] Repro check — Section 6.
7. [x] Verification + this doc.

### Notes

- `run_agent_stream` now always resolves a `SamplingConfig` (seed derived from
  `gen_id` when not given), so **every** agent path — including
  `sprite.render`'s refine pass and chat edits — runs at `0.2` + a stable seed
  by default, not just requests that opt in.
- `scoring.assess` also runs seeded (`_seed_from("score:<goal>")`) at its
  existing `0.2`.
- **Provider quirk:** langchain-openai 1.x nulls `temperature` for GPT-5-class
  models (OpenAI fixes it server-side); the seed still applies. Out of our
  control; noted, not worked around.

## 6. Verification

- **Unit (`tests/test_sampling.py`, 9 tests; 77 total green):** resolution order
  (request > phase > env); `_seed_from` deterministic + per-gen; request seed
  beats gen-id seed; every phase has a temperature and precision phases are
  colder than detail; `_get_llm` puts `seed` on the Gemini and OpenAI models;
  back-compat `temperature=` arg still works; no `temperature=0.7` literal left
  in `_get_llm`.
- **Deterministic pipeline:** already byte-identical across runs
  (`tests/test_render_core.py::test_render_deterministic`).
- **Agent stage repro (live, real Gemini):** two `run_agent_stream` runs, same
  prompt + reference, `temperature=0.0`, `seed=777`, different `gen_id` →
  **64/64 pixels identical (100%)**. Comfortably above the R6 95% bar.
- The consolidated "10 fixed tasks vs baseline" quality eval (spanning
  0001/0003/0004/0005/0006) is a follow-up eval task, not part of this merge.

## 7. Risks

- Provider seed support is inconsistent / best-effort (esp. Gemini) → treat as
  "reduce variance", not "guarantee"; R6 uses a 95% threshold for agent stages.
- Too-low temperature can make weak models loop/repeat → phase override lets
  Detail breathe at 0.35; keep `AGENT_TEMPERATURE` escape hatch.

## 8. Definition of done

- [x] `SamplingConfig` + `resolve_sampling` are the one source of sampling
  params; no `temperature=0.7` literal in `_get_llm`.
- [x] Default drawing temperature `0.2` (`AGENT_TEMPERATURE`); per-phase
  overrides via `PHASE_TEMPERATURE`, honored by `run_agent_stream(phase=)`.
- [x] Seed derived from `gen_id` (crc32) and passed to OpenAI / Gemini / Vertex
  / Ollama; `GenerateRequest.seed` overrides.
- [x] Repro check: agent-stage 100% pixel match with fixed seed + temp 0;
  deterministic pipeline already byte-identical.
- [ ] Consolidated multi-spec quality eval — deferred to a dedicated eval pass
  (all six specs are meant to be judged together).
