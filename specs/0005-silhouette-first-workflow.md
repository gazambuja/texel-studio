# Spec 0005 — Silhouette-First Workflow

- **Branch:** `spec/0005-silhouette-first-workflow` (from `main`, after 0004)
- **Status:** Implemented — merged to local `main`
- **Depends on:** 0002 (silhouette mask), 0003 (preview), 0004 (reference in context).
- **North-star link:** Structured phases produce readable sprites instead of a
  freeform poke-at-pixels loop.

---

## 1. Context

`build_system_prompt` gives one generic WORKFLOW list (`agent.py:708-718`):
"fill big areas, view, add details, view, finish". There is no enforced
structure, no silhouette gate, no per-phase success check. The ReAct agent
wanders, calls `finish` when it subjectively feels done, and often never runs
`view_canvas` enough.

## 2. Goal

Generation (refine or from-seed) proceeds through explicit phases with a gate
between each. The agent cannot advance until the current phase passes a check
(silhouette matches reference mask within tolerance; base colors placed; etc.).
Phases are short and each ends with a mandatory preview.

## 3. Requirements

### R1 — Phases
THE SYSTEM SHALL drive these phases in order:
1. **Silhouette** — establish/verify the opaque vs transparent shape.
2. **Base colors** — flat fill each region with its palette color.
3. **Shading** — 1–2 tone steps where the reference shows light/shadow.
4. **Detail** — edges, small features, readability at 1×.
5. **Cleanup** — remove strays, check tileability (block) / padding (icon).

### R2 — Gates
- WHEN the Silhouette phase ends, THE SYSTEM SHALL compare the canvas silhouette
  to the reference-derived mask (IoU); IF IoU < threshold (default 0.85 for
  seeded, 0.7 for blank), THE SYSTEM SHALL keep the agent in the Silhouette
  phase with a targeted message showing the mismatch, up to a retry cap.
- WHEN a phase's step budget is exhausted, THE SYSTEM SHALL advance anyway and
  log it.

### R3 — Per-phase budget
- Each phase SHALL have its own `max_steps` slice (defaults sum to the global
  cap). `finish` is only honored during or after the Detail phase.

### R4 — Phase-aware prompt & tools
- The system/phase prompt SHALL state only the current phase's objective and
  the allowed moves. E.g. Silhouette phase forbids per-pixel detailing tools.

### R5 — Phase progress events
- `on_step` payloads SHALL carry the current `phase` so the UI shows a phase
  tracker.

### R6 — Opt-out
- `workflow: "phased" | "freeform"` on the request (default `"phased"` for
  reference-driven, `"freeform"` otherwise). `"freeform"` == pre-0005.

## 4. Design sketch

- Implement as an orchestration layer *around* `run_agent_stream`, or as a
  LangGraph graph replacing `create_react_agent`. Prefer a thin orchestrator:
  call the agent once per phase with `existing_pixels` carried between phases and
  a phase-specific `message` + `max_steps`; keep the same `thread_id` so history
  persists.
- Gate check: `iou(canvas_silhouette, reference_mask)` in `_render_core` (mask
  already computed in 0002).
- `AGENT_PHASE_PROMPTS: dict[str, str]` in `agent.py`.
- New request field `workflow`; thread through as usual.
- Tool gating: `make_tools(..., phase=...)` filters the returned tool list.

## 5. Tasks

1. [x] `_render_core.silhouette_of(grid)` + `iou(a, b)`.
2. [x] `agent.AGENT_PHASES` (name, budget weight, instruction) for the 5 phases;
   `make_tools(phase=...)` → `_filter_phase_tools`: `silhouette` gets coarse
   shapes only (no `draw_pixel*`, no `finish`); `base_colors`/`shading` add
   pixel + noise tools but still no `finish`; `detail`/`cleanup` get everything
   incl. `finish`. `view_canvas`/`view_reference`/`get_pixel` always allowed.
3. [x] `agent.run_phased_generation(...)` — one `run_agent_stream` call per
   phase on the same `gen_id` (thread persists), carrying the grid forward via
   `existing_pixels`, each with `phase=` and `max_steps = round(global * weight)`.
4. [x] Silhouette gate: after phase 1, `iou(silhouette_of(grid), ref_mask)` vs
   `SILHOUETTE_IOU_SEEDED` (0.85) / `SILHOUETTE_IOU_BLANK` (0.70); below → re-run
   the silhouette phase with a "%-overlap, fix the outline" message, up to
   `SILHOUETTE_RETRIES` (2).
5. [x] Per-phase budgets (weights sum ~1.0, min 6 steps); `finish` only exists
   in the toolset from the `detail` phase on.
6. [x] `on_step(canvas, "phase", name)` → `phase` SSE event in `_run_agent_sse`
   + `worker` + `sprite_generate`; UI phase tracker chips in `ControlPanel`.
7. [x] `GenerateRequest.workflow` (`phased` | `freeform`, default: `phased` iff
   a reference is present) → `_run_agent_sse` / redis payload / `worker` /
   `SpriteGenerateParams`. Phased only on the first pass of a fresh generation —
   score-gate "keep drawing" rounds and chat edits stay freeform.
8. [x] Verification — Section 6.

### Deviations

- `sprite.render`'s optional `refine` pass stays freeform (it is a ≤20-step
  localised defect fix, not a from-scratch build).
- The gate only guards silhouette→rest; phases 2–5 advance on the model
  stopping or the budget. Adding gates there is a later refinement.

## 6. Verification

- **Unit (`tests/test_phases.py`, 7 tests; 68 total green):** `iou` /
  `silhouette_of`; phase tool filtering (silhouette drops pixel + finish tools,
  detail keeps finish); `run_phased_generation` (stubbed agent) calls exactly
  once per phase in order, emits the 5 `phase` markers, carries the grid
  between phases, and re-runs the silhouette phase twice when IoU stays low.
- **Live (`run_phased_generation`, real Gemini):** _see PR notes_ — phases fire
  in order, budgets respected, sprite completes.
- **Regression:** `workflow: freeform` routes straight to `run_agent_stream`
  (the 0004 path); default is `freeform` when there is no reference.

## 7. Risks

- Over-constraining small models that can't follow phase discipline → they get
  `freeform` by default (non-reference) and a looser gate threshold.
- Phase thrash (bouncing on the gate) → hard retry cap, then advance.
- Latency: 5 phase calls > 1 call. Acceptable for quality; measure and expose
  total step cap.

## 8. Definition of done

- [x] `run_phased_generation` drives the 5 phases in order on one thread with an
  IoU silhouette gate + bounded retries.
- [x] Per-phase step budgets; `finish` absent from the toolset before `detail`.
- [x] UI phase-tracker chips (`ControlPanel`), fed by `phase` SSE events.
- [x] `workflow: freeform` (and the no-reference default) == pre-0005 path.
- [x] 68 tests green; live phased run verified.
- [ ] The full "10 fixed tasks vs freeform" quality bar is deferred to a
  dedicated eval pass once 0006 (sampling) lands — the pieces (0003 preview,
  0004 context, 0005 phases, 0006 determinism) are meant to be judged together.
