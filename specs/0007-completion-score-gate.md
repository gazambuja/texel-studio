# Spec 0007 — Completion Score Gate

- **Branch:** `spec/0007-completion-score-gate` (from `main`, after 0001)
- **Status:** Planned — **runs next, before 0002** (user-requested mid-0001)
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

## 5. Tasks (outline)

1. [ ] `agent/_score.py`: `assess()` + `AssessmentScore` schema + a test with a
   stub LLM (monkeypatch `_get_llm`) asserting parse + threshold logic.
2. [ ] DB columns `score`, `score_reason` (+ migration).
3. [ ] Score-gate wrapper: run assess at end-of-draw, decide per `on_low_score`.
4. [ ] `needs_decision` SSE event + `POST /continue_drawing` resuming the thread
   with the gap list and `extra_steps`.
5. [ ] `max_score_rounds` bound.
6. [ ] Request/params fields through `server.py` + `worker.py` + handlers.
7. [ ] Standalone UI: score badge + decision prompt ("+100 steps" / "good
   enough"); rebuild `static/`.
8. [ ] Verification.

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

- [ ] Every agent-drawing completion carries an LLM closeness `score` + reason +
  gaps, persisted and on the result event.
- [ ] Below threshold: interactive clients are asked to add N steps; approving
  resumes the same thread with the gap list; declining finalizes.
- [ ] `on_low_score` auto/ignore modes for headless use; `max_score_rounds`
  bounds cost.
- [ ] Standalone UI shows the score and the continue/finish choice.
- [ ] Cost delta per generation measured and recorded in the PR.
- [ ] Regression: disabling the gate reproduces the pre-0007 flow.
