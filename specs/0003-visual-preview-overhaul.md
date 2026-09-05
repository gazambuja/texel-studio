# Spec 0003 — Visual Preview Overhaul

- **Branch:** `spec/0003-visual-preview-overhaul` (from `spec/0002-reference-seeded-canvas`)
- **Status:** Planned
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

## 5. Tasks (outline)

1. [ ] `_preview.py` with `build_preview_image` / `build_preview_text` + tests
   (image dimensions, panel count with/without reference).
2. [ ] Move `render_grid_overlay` into shared module; repoint `server.py`.
3. [ ] Pass `reference_img` + `seed_grid` into `make_tools`.
4. [ ] Image-as-message plumbing in `run_agent_stream` stream loop.
5. [ ] ASCII trim above size 32.
6. [ ] Diff tint from seed.
7. [ ] Non-vision path: keep ASCII, add clearer ruler + explicit
   "row y / column x" restatement.
8. [ ] Verification.

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

- [ ] `view_canvas` delivers a real composited image part to vision models.
- [ ] Triptych includes the reference and a coordinate-labeled panel.
- [ ] ASCII grid trimmed above size 32; non-vision view improved.
- [ ] Seed-diff tint visible.
- [ ] Token/latency delta measured and documented in the PR.
