# Spec 0005 — Silhouette-First Workflow

- **Branch:** `spec/0005-silhouette-first-workflow` (from `spec/0004-persistent-reference-context`)
- **Status:** Planned
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

## 5. Tasks (outline)

1. [ ] `iou()` + mask compare in `_render_core` + tests.
2. [ ] Phase prompt table + phase-filtered `make_tools`.
3. [ ] Orchestrator: per-phase agent invocation, carrying pixels + thread.
4. [ ] Silhouette gate with retry cap + mismatch message (uses 0003 preview).
5. [ ] Per-phase budgets; `finish` gating.
6. [ ] `phase` in progress events; UI phase tracker.
7. [ ] `workflow` request field + routing.
8. [ ] Verification.

## 6. Verification

- Unit: gate holds the agent when IoU low, releases when high; budget exhaustion
  advances + logs.
- Manual: 10 fixed reference tasks in phased mode vs freeform — phased should
  win on "silhouette matches" and "reads at 1×" judgments.
- Regression: `workflow: freeform` == 0004 behavior.

## 7. Risks

- Over-constraining small models that can't follow phase discipline → they get
  `freeform` by default (non-reference) and a looser gate threshold.
- Phase thrash (bouncing on the gate) → hard retry cap, then advance.
- Latency: 5 phase calls > 1 call. Acceptable for quality; measure and expose
  total step cap.

## 8. Definition of done

- [ ] Five-phase orchestration with a working silhouette gate.
- [ ] Per-phase budgets and `finish` gating enforced.
- [ ] UI phase tracker.
- [ ] `freeform` opt-out matches pre-0005.
- [ ] Quality comparison recorded in the PR.
