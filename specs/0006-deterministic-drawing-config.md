# Spec 0006 — Deterministic Drawing Config

- **Branch:** `spec/0006-deterministic-drawing-config` (from `spec/0005-silhouette-first-workflow`)
- **Status:** Planned
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

## 5. Tasks (outline)

1. [ ] `SamplingConfig` + resolution order + unit tests.
2. [ ] Rework `_get_llm` to consume it; per-provider seed wiring.
3. [ ] Default `0.2`; env `AGENT_TEMPERATURE`.
4. [ ] Phase temperature column in the 0005 phase table.
5. [ ] `GenerateRequest.temperature` / `.seed` passthrough.
6. [ ] Repro test harness: run a fixed request twice, diff `pixel_data`.
7. [ ] Verification + doc.

## 6. Verification

- Unit: resolution order (request beats phase beats env beats default); seed
  reaches each provider's kwargs.
- Manual: same request ×2 with seed+temp0 → deterministic-stage output
  byte-identical; agent-stage ≥ 95% pixel match.
- Quality: re-run the 0001/0003/0005 "10 fixed tasks" bars now that they're
  reproducible; record numbers.

## 7. Risks

- Provider seed support is inconsistent / best-effort (esp. Gemini) → treat as
  "reduce variance", not "guarantee"; R6 uses a 95% threshold for agent stages.
- Too-low temperature can make weak models loop/repeat → phase override lets
  Detail breathe at 0.35; keep `AGENT_TEMPERATURE` escape hatch.

## 8. Definition of done

- [ ] Single sampling-config source; no stray `temperature=` literals in
  `_get_llm`.
- [ ] Default drawing temperature `0.2`, per-phase overrides honored.
- [ ] Seed passed to every provider that supports it, derived from `gen_id`.
- [ ] Reproducibility check passes at the stated thresholds.
- [ ] Prior specs' quality bars re-measured with fixed seeds and recorded.
