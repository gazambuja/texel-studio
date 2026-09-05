# Spec 0003 — Visual Preview Overhaul

- **Branch:** `spec/0003-visual-preview-overhaul` (from `main`, after 0002)
- **Status:** Implemented — merged to local `main`
- **Depends on:** 0002 (silhouette mask, seed diff).
- **North-star link:** The refinement agent can finally *see* what it made and
  compare it to the reference.

---

## 1. Context

`agent.view_canvas` (`agent.py:442-474`) returns:
- a dense single-char ASCII grid with a 2-line numeric column ruler,
- a legend + quadrant "LAYOUT" text summary,
- for vision models, `canvas.to_image_b64(64)` — a **hard-coded 64×64** PNG
  **embedded as base64 text inside the tool result** (`agent.py:370-374`).

Problems: at 64/128px the image is a downscale; base64-in-text gets far less
model attention than a real image part; there are no coordinate axes on the
image; the ASCII grid is near-unreadable for spatial reasoning; the reference is
nowhere in the view.

`server.render_grid_overlay` (`server.py:402-429`) already renders a coordinate
grid overlay but is only used by the dead "assessment" path.

## 2. Goal

`view_canvas` returns a **single composited image, passed as a proper image
content part**, showing: the reference (left) | the current canvas (middle) |
the canvas with a coordinate grid + axis labels (right), each NEAREST-upscaled
to a legible size proportional to canvas size (target ~256–384px per panel). The
ASCII grid stays as a compact secondary text aid but is trimmed. Non-vision
models keep an improved ASCII-only view.

## 3. Requirements

### R1 — Real image part
- WHEN a vision model calls `view_canvas`, THE SYSTEM SHALL return the preview
  as a LangChain image content block (`{"type": "image_url", ...}` on a
  `ToolMessage`/`HumanMessage` as supported), NOT as base64 text in the string.
- IF the model/runtime cannot accept tool-returned images, THE SYSTEM SHALL fall
  back to injecting the image as a follow-up `HumanMessage` after the tool
  result.

### R2 — Composite triptych
- The preview image SHALL contain, when a reference is available: reference |
  canvas | canvas+grid, side by side, each labeled.
- WHEN no reference is available: canvas | canvas+grid.
- Each panel SHALL be NEAREST-upscaled to `clamp(canvas_size * k, 192, 512)` px,
  `k` chosen so a 32px canvas → ~320px panel.

### R3 — Coordinate overlay
- The grid panel SHALL draw gridlines every pixel (faint) and every 8px
  (stronger) with x labels across the top and y labels down the left, reusing
  `render_grid_overlay` logic (moved into a shared module).

### R4 — Trimmed ASCII
- The text portion SHALL keep the char grid only for canvas_size ≤ 32; above
  that it SHALL return just the legend + region summary + a pointer to the
  image. (Large ASCII grids waste tokens and mislead.)

### R5 — Diff highlight (uses 0002)
- WHEN seeded, the canvas panels SHALL tint pixels changed since the seed so the
  model sees its own edits.

### R6 — Config
- Panel size, whether to include the triptych, and ASCII threshold SHALL be
  constants in one place, overridable by env for experimentation.

## 4. Design sketch

- New `preview.py` (or `agent/_preview.py`): `build_preview_image(canvas,
  reference_img, seed_grid) -> PIL.Image` and `build_preview_text(canvas) -> str`.
  Move `render_grid_overlay` + `upscale_image` usage here.
- `make_tools` closure gets `reference_img` and `seed_grid` passed in from
  `run_agent_stream` (both already available there).
- `view_canvas` returns text; the stream loop in `run_agent_stream`
  (`agent.py:815-836`) detects a `view_canvas` tool result and appends the image
  as the next input message on the following turn (works across providers).
- Token budget: triptych PNG at 3×320px is well within vision limits; ASCII trim
  offsets the cost.

## 5. Tasks

1. [x] `preview.py` — `build_preview_image(canvas, reference_img)` (labelled
   triptych, `_scale_for` clamp formula, edit tint) + `build_preview_text(canvas)`.
   `tests/test_preview.py` (7 tests).
2. [~] Grid-overlay logic lives in `preview.py` (`_sprite_panel(grid_overlay=True)`).
   `server.render_grid_overlay` / `build_assessment_context` were already dead
   (no callers) — **left in place**, not repointed; a cleanup pass can remove
   them.
3. [x] `run_agent_stream` decodes `reference_b64` → PIL once, passes
   `reference_img` to `make_tools` and to the hook.
4. [x] Image-as-message via a `create_react_agent` **`pre_model_hook`**
   (`agent._make_preview_hook`) — after a `view_canvas` `ToolMessage`, append a
   `HumanMessage` carrying the triptych image (R1 fallback path, works for
   Gemini/OpenAI/Ollama alike since it is a human turn, not a tool-role image).
5. [x] ASCII grid only for `canvas.size <= PREVIEW_ASCII_MAX` (32); larger
   canvases get "read the attached preview image" + axis restatement.
6. [x] Edit tint: `preview._edits(canvas)` diffs `canvas.pixels` vs
   `canvas.seed_pixels` (0002); tinted on the CURRENT and GRID panels.
7. [x] Non-vision path: `view_canvas` returns `build_preview_text`, which now
   prefixes the ASCII grid with an explicit "top digits = x, left digits = y"
   restatement.
8. [x] Verification — Section 6.

### Config (R6)

`PREVIEW_PANEL_PX` (320), `PREVIEW_PANEL_MIN` (192), `PREVIEW_PANEL_MAX` (512),
`PREVIEW_ASCII_MAX` (32) — env-overridable constants in `preview.py`.

### Token guardrail

The hook `RemoveMessage`s the previous preview `HumanMessage` when injecting a
new one, so **at most one triptych image** sits in context at a time. (Spec
0004 generalises this to the reference + a small ring of recent previews.)

## 6. Verification

- Unit: preview image has 3 panels with a reference, 2 without; panel px size
  matches the clamp formula for sizes 8/16/32/64/128.
- Manual: run a refine job with logging of the exact messages sent to the LLM;
  confirm a real image part reaches the model each `view_canvas`.
- Quality: on 10 fixed refine tasks, count of "agent corrected a real defect it
  could see" goes up vs 0002 baseline.

## 7. Risks

- Provider differences in accepting tool-returned images — R1 fallback covers it;
  test Gemini + OpenAI + one Ollama vision model.
- Larger previews = more tokens/latency per step. Mitigated by ASCII trim and a
  step cap; measure tokens before/after.

## 8. Definition of done

- [x] `view_canvas` → the model receives a real composited image part (via the
  `pre_model_hook` `HumanMessage`), not base64-in-text.
- [x] Triptych: `[reference] | current | current+coordinate-grid`, each labelled,
  NEAREST-upscaled by the clamp formula (32px→320px panel; unit-tested for
  8/16/32/64/128).
- [x] ASCII grid only ≤ 32px; non-vision view prefixes an x/y axis restatement.
- [x] Seed-diff tint on the current + grid panels (unit-tested via `_edits`).
- [x] Token guardrail: ≤ 1 triptych image retained in context (hook
  `RemoveMessage`s the prior one). Panel px is env-tunable for further tuning.
- [x] 53 tests green.

### Deviation

- Full token/latency measurement across providers is deferred to 0004 (which
  owns the image-context budget). 0003's guardrail (one image max) keeps the
  delta bounded: one triptych PNG replaces the old 64² base64 blob + the large
  ASCII grid that is now trimmed on big canvases — roughly net-neutral on
  tokens, materially better signal.
