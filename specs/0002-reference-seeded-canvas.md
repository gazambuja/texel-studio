# Spec 0002 — Reference-Seeded Canvas

- **Branch:** `spec/0002-reference-seeded-canvas` (from `spec/0001-image-first-pipeline`)
- **Status:** Planned
- **Depends on:** 0001 (`_render_core.quantize`, seed-aware agent prompt).
- **North-star link:** The refinement agent stops guessing geometry; it edits a
  correct starting image.

---

## 1. Context

Today the agent path (`agent.run_agent_stream`, `jobs/sprite_generate.py`)
starts every generation from a **blank** `Canvas` (`agent.py:751`,
`existing_pixels=None`). Even with a reference attached, the model must
reconstruct the whole composition from nothing. Spec 0001 adds a minimal "seed
from quantized grid" hook but only inside `sprite.render`'s optional refine
pass. This spec makes **seeded start a first-class mode** for the agent path and
improves the seed itself (ghost/underlay, locked regions).

## 2. Goal

When a reference exists, the agent always begins from a quantized underlay of
that reference at canvas resolution. Its job becomes *correction and stylization*
— fix edges, add readable detail, fix palette misassignments — not construction.
The user can mark the seed as a locked base (silhouette preserved) or a soft
suggestion.

## 3. Requirements

### R1 — Seeded start
- WHEN the agent path runs with a `reference_id`, THE SYSTEM SHALL initialize the
  `Canvas` with `_render_core.quantize(prepare(ref))` output instead of blanks.
- WHEN seeded, the system prompt SHALL state the canvas already contains a
  first-pass conversion of the reference and the task is to refine it.

### R2 — Seed strength
- The request SHALL accept `seed_mode`: `"locked"` | `"soft"` | `"off"`
  (default `"soft"`).
- WHEN `"locked"`, THE SYSTEM SHALL reject (no-op + tool message) any drawing
  operation that changes a pixel currently inside the reference silhouette from
  opaque to `-1` or vice versa — silhouette is immutable, colors are editable.
- WHEN `"soft"`, all edits allowed; the prompt frames the seed as a suggestion.
- WHEN `"off"`, behave as pre-0002 (blank canvas).

### R3 — Silhouette mask
- THE SYSTEM SHALL compute a boolean silhouette mask from the seed (opaque vs
  transparent after background removal) and expose it to `Canvas` for R2
  enforcement and for a new `view_reference()` tool (see 0004).

### R4 — Diff-aware progress
- Progress events SHALL include which pixels changed since the seed so the UI
  can highlight agent edits vs the underlay.

## 4. Design sketch

- `agent.Canvas.__init__` already takes `pixels`; pass the seed grid from the
  job handlers (`sprite_generate.py`, `sprite_chat.py`, `_run_agent_sse`).
- Add `Canvas.silhouette: set[tuple[int,int]] | None` + a `locked` flag; guard
  in `set_pixel`/`fill_*`/`draw_*` when `locked`.
- Seed construction reuses `_render_core` entirely — no new image code.
- `build_system_prompt` gains a `seeded: bool` branch with new WORKFLOW text
  (verify → fix edges → fix colors → add detail → finish).
- New `GenerateRequest.seed_mode`; thread through `server.py` + `worker.py` +
  all agent-invoking job handlers.

## 5. Tasks (outline)

1. [ ] `Canvas` silhouette + lock enforcement + unit tests.
2. [ ] Seed builder helper `jobs/_seed.py` (ref_id → grid + mask).
3. [ ] `build_system_prompt(seeded=...)` variant.
4. [ ] Thread `seed_mode` through API/worker/handlers.
5. [ ] Diff tracking in `on_step` payloads.
6. [ ] UI: `seed_mode` selector; underlay vs edits rendering.
7. [ ] Verification (Section 6).

## 6. Verification

- Unit: locked mode blocks silhouette-breaking ops; soft mode allows them.
- Manual: reference of a mushroom → seeded soft → agent spends its steps on
  outline + eyes, not on blocking the cap shape. Locked mode: silhouette
  pixel-identical to seed at finish.
- Regression: `seed_mode: off` == pre-0002 output.

## 7. Risks

- Lock enforcement fighting legitimate edits near the edge → keep it silhouette
  (opaque/transparent) only, never color.
- Seed quality ceiling — if 0001 quantize is bad, seed is bad. Mitigated by
  0001's cleanup + `seed_mode: soft` default.

## 8. Definition of done

- [ ] Agent path starts from a reference-derived seed by default.
- [ ] `locked` / `soft` / `off` all behave per spec, unit-tested.
- [ ] UI exposes the mode and distinguishes underlay from agent edits.
- [ ] Regression: `off` matches pre-0002.
