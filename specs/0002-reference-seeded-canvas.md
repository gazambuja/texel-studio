# Spec 0002 — Reference-Seeded Canvas

- **Branch:** `spec/0002-reference-seeded-canvas` (from `main`, after 0007)
- **Status:** Implemented — merged to local `main`
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

## 5. Tasks

1. [x] `agent.Canvas`: `silhouette` set + `locked` flag + `_put()` write choke
   point (all 14 write sites routed through it) + `lock_hits`. Locked mode
   blocks opaque↔transparent flips across the silhouette; recolour inside stays
   free.
2. [x] `jobs/_seed.py`: `build_seed(img, size, type, palette) -> (grid, mask)`
   and `build_seed_from_b64(...)`, reusing `_render_core`.
3. [x] `run_agent_stream` builds the seed when `is_new` + reference +
   `seed_mode in {soft,locked}` + no existing pixels; seeded system-prompt
   branch with soft/locked wording + "don't clear the canvas".
4. [x] `seed_mode` threaded: `GenerateRequest` (default `"soft"`) →
   `_run_agent_sse` → `run_agent_stream`; redis `{"type":"generate"}` payload →
   `worker.handle_generate`; `SpriteGenerateParams` → `sprite.generate` handler
   (round 0 only).
5. [x] `canvas.seed_pixels` snapshot; `pixels` SSE events carry `seed_pixels`
   when seeded.
6. [x] UI: `seed: soft|locked|off` selector in `ControlPanel`; `useStudio`
   tracks `seedPixels`; `Canvas.tsx` tints cells changed vs the underlay.
   `static/` rebuilt.
7. [x] Verification — Section 6.

### Deviations / findings

- Only the **explicit `mode: agent`** path is seeded. `mode: auto` + reference
  goes through 0001's image-first pipeline (which already *is* a quantized
  render), so it needs no seed.
- **Observed:** in `soft` mode a weak model may still wipe the seed on its first
  move (a `fill` with `-1`). The prompt now forbids this, but `soft` is by
  definition permissive — `locked` is the guarantee, and the 0007 score gate
  catches a bad result. Default stays `soft` per the spec.
- `sprite_chat` (edits) is not seeded — it already has pixels.

## 6. Verification

- **Unit (`tests/test_seed.py`, 12 tests, 46 total green):**
  - `build_seed` → transparent bg + mask == opaque cells for `icon`; full fill
    for `block`; `build_seed_from_b64` round-trips.
  - `Canvas` locked: blocks erasing a silhouette pixel, blocks extending the
    silhouette, allows recolour inside; `fill_rect` on a locked canvas touches
    only silhouette cells. Soft mode allows reshaping. Unlocked/no-silhouette
    canvas is byte-for-byte unchanged behaviour.
  - `run_agent_stream` (stubbed agent): `soft` seeds a non-blank canvas with a
    silhouette + `seed_pixels`; `locked` sets the flag; `off` leaves it blank
    with no silhouette.
- **Live:** mushroom reference, `mode: agent`, `seed_mode: soft`, size 16 → the
  first `pixels` SSE event has **84 non-blank pixels** (the underlay) and
  carries `seed_pixels`. (Also surfaced the soft-wipe finding above.)
- **Regression:** `seed_mode: off` → blank canvas, `silhouette is None` — the
  pre-0002 code path.

## 7. Risks

- Lock enforcement fighting legitimate edits near the edge → keep it silhouette
  (opaque/transparent) only, never color.
- Seed quality ceiling — if 0001 quantize is bad, seed is bad. Mitigated by
  0001's cleanup + `seed_mode: soft` default.

## 8. Definition of done

- [x] Agent path (`mode: agent`) starts from a reference-derived seed by default
  (`seed_mode` defaults to `"soft"`).
- [x] `locked` / `soft` / `off` behave per spec, unit-tested (`_put` choke point
  + `tests/test_seed.py`).
- [x] UI exposes `seed: soft|locked|off` and tints agent edits vs the underlay
  (`Canvas.tsx` using `seed_pixels`).
- [x] Regression: `off` == pre-0002 (blank canvas, no silhouette); 46 tests
  green; existing Canvas behaviour unchanged when no silhouette is set.
