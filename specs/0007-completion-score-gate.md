# Spec 0007 — Completion Score Gate

- **Branch:** `spec/0007-completion-score-gate` (from `main`, after 0001)
- **Status:** Implemented — merged to local `main`
- **Depends on:** 0001 (refine pass, `existing_pixels` continuation).
  Benefits later from 0003 (a preview the scorer can actually read).
- **North-star link:** Nothing is delivered as "done" until it has been checked
  against what the user actually asked for.

---

## 1. Context

Both drawing paths finish on the agent's own say-so: the ReAct agent calls
`finish()` (or hits `max_steps`) and `run_agent_stream` returns
(`agent.py` stream loop, `FINISHED` / `step_count >= max_steps`). Nobody checks
whether the result resembles the request. The 0001 `refine` pass has the same
open end. Users get a "Complete!" status on sprites that clearly miss.

## 2. Goal

Before a drawing job (agent path or 0001 refine path) is reported complete, an
LLM is shown **the final rendered image + the user's goal (+ the reference if
any)** and returns a **closeness score 0–100** with a one-line reason and a
short list of concrete gaps. If the score is below a threshold, the job does not
silently finish: the user is asked whether to **keep drawing for N more steps**
(default +100, configurable). Accepting resumes the *same* agent thread with the
gap list as the instruction; declining finalizes as-is.

Deterministic image-first jobs with **no** refine pass are not scored (there is
no agent to continue) — but the score is still computed and surfaced as
information.

## 3. Requirements

### R1 — Final assessment
- WHEN a drawing job reaches its natural end (agent `finish`, or step budget
  exhausted, or 0001 refine pass ends), THE SYSTEM SHALL call a scoring LLM with:
  the user's original prompt/goal, the sprite type, the reference image (if
  any), and the final sprite rendered as a NEAREST-upscaled PNG image part.
- The scorer SHALL return structured output: `score` (int 0–100),
  `reason` (str), `gaps` (list[str], ≤5, each a concrete fixable item).
- The scoring model SHALL default to the generation model, overridable by
  `score_model`; scoring SHALL use `temperature <= 0.2`.

### R2 — Threshold + decision point
- `score_threshold` (default 70, request-overridable).
- WHEN `score >= score_threshold` OR scoring failed/unavailable, THE SYSTEM
  SHALL finalize normally and include the score in the completion event.
- WHEN `score < score_threshold`, THE SYSTEM SHALL emit a
  `needs_decision` event carrying `score`, `reason`, `gaps`, and the proposed
  `extra_steps` (default 100), and SHALL pause without finalizing.

### R3 — User decision
- THE SYSTEM SHALL expose `POST /api/generations/{id}/continue_drawing`
  `{ approve: bool, extra_steps?: int }`.
- WHEN `approve` is true, THE SYSTEM SHALL resume the **same** agent thread
  (continuation: thread already exists in the checkpointer) with a message built
  from `gaps` ("Improve the sprite. Specific gaps to fix: …"), `max_steps =
  extra_steps`, then re-run the score gate (R1) once more.
- WHEN `approve` is false, THE SYSTEM SHALL finalize the current pixels as-is.
- The gate SHALL re-trigger at most `max_score_rounds` times (default 2) to
  bound cost, then finalize regardless.

### R4 — Auto-continue (headless / cloud)
- `on_low_score`: `"ask"` (default) | `"auto"` | `"ignore"`.
  `"auto"` skips the user round-trip and continues `extra_steps` immediately
  (bounded by `max_score_rounds`); `"ignore"` finalizes and just records the
  score. Cloud jobs with no interactive client default to `"auto"`.

### R5 — Surfacing
- The completion/`result` event SHALL always include `score`, `reason`, `gaps`.
- The standalone UI SHALL show the score on completion, and when a
  `needs_decision` event arrives, show reason + gaps and a
  **"draw 100 more steps"** / **"good enough"** choice wired to R3.

### R6 — Persistence
- `generations` SHALL store `score`, `score_reason` (add columns; migration in
  `server._init_db` following the existing `_has_column` pattern).

## 4. Design sketch

- New `agent/_score.py` (or `scoring.py`):
  `assess(goal, sprite_type, reference_img, final_img, model, temperature)
  -> ScoreResult(score, reason, gaps)`. Uses the same `_get_llm` factory with
  structured output (pydantic `AssessmentScore`). Reuse
  `server.PixelGenerationResponse`-style schema pattern.
- `run_agent_stream` gains an optional `score_fn` callback invoked right before
  return; it yields a synthetic `on_step(canvas, "score", json)` so existing
  SSE plumbing carries it. Actual pause/resume is orchestrated one level up
  (job handler / `_run_agent_sse` / `_run_render_sse`), not inside the stream
  loop.
- Pause mechanics: the job emits `needs_decision` and **ends the SSE stream**
  (like the current "finalize" flow). The client calls
  `/continue_drawing`, which starts a fresh SSE stream that resumes the thread.
  This reuses the existing continuation path (`thread_exists` → follow-up
  message) — no long-lived server-side suspension.
- `GenerateRequest` + `SpriteRenderParams`: `score_threshold`, `score_model`,
  `extra_steps`, `on_low_score`, `max_score_rounds`.
- Score-gate wrapper shared by `sprite_generate`, `sprite_chat`,
  `sprite_render` (refine), `_run_agent_sse`, `_run_render_sse`.

## 5. Tasks

1. [x] `scoring.py` — `AssessmentScore` schema, `assess()` (vision call, clamps
   score, drops empty gaps), `decide()` (pure policy), `ScoreConfig`
   (+`from_request`, interactive vs headless default), `run_gate()`,
   `gaps_to_instruction()`.
2. [x] DB columns `score`, `score_reason`, `score_rounds` (+ `_has_column`
   migration).
3. [x] Score gate in `_run_agent_sse` (self-hosted agent path): loop that
   re-runs the agent as a continuation on `action=="continue"`; emits
   `needs_decision` and stops on `action=="ask"`. Same gate in `_run_render_sse`
   (0001 refine) and `jobs/sprite_generate.py` (headless → `auto`).
4. [x] `needs_decision` SSE event + `POST /api/generations/{id}/continue_drawing`
   `{approve, extra_steps?, gaps?}` — resumes the thread with
   `gaps_to_instruction`, `max_steps=extra_steps`, re-runs the gate.
5. [x] `max_score_rounds` bound (default 2) enforced in `decide()`;
   `/continue_drawing` bumps `score_rounds`.
6. [x] `GenerateRequest` + `SpriteGenerateParams` fields
   (`score`, `score_threshold`, `score_model`, `on_low_score`, `extra_steps`,
   `max_score_rounds`); env defaults `SCORE_THRESHOLD` / `SCORE_EXTRA_STEPS` /
   `SCORE_MAX_ROUNDS`.
7. [x] Standalone UI (`useStudio.ts` + `ControlPanel.tsx`): `pendingScore`
   state, `needs_decision` handling, score shown on `complete`,
   `continueDrawing(approve, extraSteps)` streaming the continue response,
   "draw N more steps" / "good enough" buttons. `static/` rebuilt.
8. [x] Verification — Section 6.

### Deviations

- `worker.handle_generate` (the legacy Redis `{"type":"generate"}` path used by
  `/api/generate` in scaled mode for the *agent* path) does **not** score. The
  generic `/api/jobs` → `sprite.generate` handler does. Legacy-Redis-agent
  scoring is a follow-up (can't be exercised without a Redis worker here).
- `sprite_chat` (edits) is not gated — scoring runs on initial generation and
  the 0001 refine pass only.
- Scoring model failure / no vision model ⇒ `run_gate` returns `None` ⇒ finalize
  normally (verified live with a real low score, and with a stubbed failure).

## 6. Verification

- Unit: assess() parses a stubbed response; `score >= threshold` finalizes;
  `score < threshold` with `on_low_score="ask"` emits `needs_decision` and does
  not finalize; `"auto"` continues; `max_score_rounds` stops the loop.
- Manual: generate a deliberately hard sprite → low score → UI offers +100 →
  approving resumes the same thread and the second score is ≥ the first.
- Regression: `on_low_score="ignore"` (or scoring disabled) === pre-0007 flow
  plus a score field on the result.

## 7. Risks

- **Cost/latency**: one extra vision call per draw, more per continue round.
  Bounded by `max_score_rounds`; `score_threshold` tunable; skip when no vision
  model. Measure and document.
- **Scorer noise**: LLM scores are not calibrated. Treat the number as a coarse
  gate (default 70), not a metric; always show `reason`+`gaps` so the user
  judges. Low temperature + structured output.
- **Resume drift**: continuing 100 steps can regress a decent sprite. Mitigation:
  the resume prompt is the concrete `gaps` list only, plus "do not restyle what
  already works"; 0002's locked-silhouette mode helps once it lands.
- **UX pause**: ending the SSE stream to ask is a visible stop. Acceptable and
  consistent with the existing finalize flow; `on_low_score="auto"` avoids it
  where interactivity isn't available.

## 8. Definition of done

- [x] Agent-drawing + 0001-refine completions carry an LLM `score` + `reason` +
  `gaps`, persisted (`generations.score/score_reason/score_rounds`) and on the
  `complete` event. *(Verified: gen 21 score 15, gen 18 score 45.)*
- [x] Below threshold + interactive: `needs_decision` event, no finalize;
  `POST /continue_drawing {approve:true}` resumes the same agent thread with the
  gap list; `{approve:false}` finalizes. *(Verified live: decline → 200
  `{status:"complete"}`.)*
- [x] `on_low_score` `auto` (headless default, loops) / `ignore` (finalize +
  record) modes; `max_score_rounds` bounds the loop. *(Unit-tested in
  `tests/test_scoring.py`.)*
- [x] Standalone UI shows the score and a "draw N more steps" / "good enough"
  choice; `static/` rebuilt (`next build` green).
- [x] 34 tests green.
- [x] Regression: `score:false` (or model unavailable) ⇒ pre-0007 flow plus a
  null score column. *(`run_gate` returns None ⇒ original finalize path.)*
- **Cost note:** one extra vision call per completion (+ one per continue
  round). At 256px NEAREST upscale + `temperature=0.2`, structured output.
  Bounded by `max_score_rounds`; disable with `score:false` or
  `SCORE_THRESHOLD=0`.
