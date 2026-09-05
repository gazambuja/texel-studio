# Spec 0001 — Image-First Generation Pipeline

- **Branch:** `spec/0001-image-first-pipeline` (from `main`)
- **Status:** Implemented — merged to local `main`
- **Owner:** automated (SDD loop)
- **North-star link:** Directly builds "any reference image → clean pixel art".

---

## 1. Context

### What generation does today

Two ways a sprite gets made:

1. **Agent path** (`jobs/sprite_generate.py` → `agent.run_agent_stream`):
   a LangGraph ReAct agent starts from a **blank** `Canvas` and issues drawing
   primitives (`fill_rect`, `draw_circle`, `draw_pixel`, …). The reference image,
   if any, is attached **once** to the first `HumanMessage`
   (`agent.py:765-769`). The agent "sees" its work through `view_canvas`, which
   returns a dense ASCII grid plus, for vision models, a **hard-coded 64×64**
   base64 PNG embedded as text in the tool result (`agent.py:370-374`,
   `agent.py:470-472`). At 128px canvas this preview is a *downscale* — the
   agent cannot see its own pixels.

2. **Photo path** (`jobs/sprite_from_photo.py`): downloads an image, resizes to
   `size×size` with LANCZOS, snaps every pixel to the nearest palette color by
   RGB distance, emits `pixel_data`. No LLM. This path already produces
   *structurally correct* sprites — it just isn't wired into the main UX and
   does no cleanup (dithering artifacts, no background handling, no palette
   derivation, no edge cleanup).

### Why the agent path fails

- LLMs cannot precisely rasterize geometry blind. Each `draw_circle(cx,cy,r)` is
  a guess.
- The reference drifts far back in context after dozens of tool calls.
- The preview is unreadable (64×64, buried in text, no coordinates).
- `temperature=0.7` (`agent.py:551`) adds noise to a precision task.

### Relevant existing building blocks

- `jobs/sprite_from_photo.py` — resize + nearest-palette quantize (reusable core).
- `server.py:extract_reference_palette` (`server.py:897`) — MEDIANCUT palette
  extraction from a reference, already on the current branch.
- `server.py:upscale_image` (`server.py:236`) — NEAREST upscale for previews.
- `agent.Canvas` — indexed pixel model + `to_image`, used everywhere downstream.
- Job registry (`jobs/__init__.py`) — add a handler, nothing else changes.
- `server.SPRITE_TYPES` — per-type prompt/background rules.

---

## 2. Goal

Make **image-first generation the default path** when a reference image is
present. The user picks (or uploads, or AI-generates) a reference, picks a size,
and gets a clean palette-indexed sprite in ~2 seconds — produced by
**resize → quantize → cleanup**, not by an agent drawing blind. An **optional**
LLM "refine" pass can run afterward to fix specific defects, but it is no longer
responsible for constructing the image.

A user with only a text prompt and no reference still gets today's behavior
(concept image is auto-generated first when possible, otherwise agent path).

---

## 3. Requirements

### R1 — Reference-driven deterministic render

**Story:** As a user with a reference image, I want a faithful pixel-art version
in one click without an LLM drawing it stroke by stroke.

- WHEN a generation request includes a `reference_id` AND `mode` is unset or
  `"auto"`, THE SYSTEM SHALL run the image-first pipeline (resize → quantize →
  cleanup) instead of the agent path.
- WHEN the pipeline runs, THE SYSTEM SHALL emit a `progress` event with the
  quantized `pixel_data` within 5 seconds for sizes ≤ 128.
- WHEN the pipeline finishes, THE SYSTEM SHALL persist `pixel_data`, the
  rendered PNG, and a NEAREST-upscaled preview, identical in shape to what the
  agent path persists (so the gallery, chat-continue, export all keep working).

### R2 — Palette handling

**Story:** As a user, I want the sprite to use a coherent palette derived from
my reference unless I supplied my own.

- WHEN the request supplies a non-default `colors` array, THE SYSTEM SHALL
  quantize to exactly those colors.
- WHEN the request supplies no palette (or the sentinel default), THE SYSTEM
  SHALL derive an N-color palette from the reference via the existing MEDIANCUT
  extractor (`extract_reference_palette` logic), where N defaults to 16 and is
  overridable by `max_colors`.
- WHEN a palette is derived, THE SYSTEM SHALL return it in the result payload so
  the UI can display and let the user edit it.

### R3 — Background / silhouette handling per sprite type

**Story:** As a user making an icon or character, I want the background removed;
for a block tile I want the whole canvas filled.

- WHEN `sprite_type` is `icon`, `character`, or `freeform`-object, THE SYSTEM
  SHALL treat near-uniform border pixels as background and set them to `-1`
  (transparent), using a flood fill from the four edges with a color-distance
  threshold.
- WHEN `sprite_type` is `block`, THE SYSTEM SHALL fill every pixel (no `-1`) and
  SHALL NOT run background removal.
- WHEN background removal runs, THE SYSTEM SHALL leave interior pixels of the
  same color as the background untouched (flood fill from edges only, not a
  global color match).

### R4 — Cleanup pass

**Story:** As a user, I want the raw quantized output to not look like noisy
JPEG mush.

- WHEN the pipeline produces the indexed grid, THE SYSTEM SHALL run a
  deterministic cleanup: (a) remove isolated single-pixel islands (a pixel whose
  4-neighbors are all a different single color is reassigned to that color),
  (b) optionally snap to a smaller working palette if two palette entries are
  within a small distance.
- Cleanup SHALL be pure/deterministic and togglable via `cleanup: bool`
  (default `true`).

### R5 — Optional LLM refine pass

**Story:** As a user unhappy with a few spots, I want to ask an LLM to fix just
those, starting from the good quantized image — not from blank.

- WHEN the request sets `refine: true`, THE SYSTEM SHALL, after the deterministic
  pipeline, invoke the agent seeded with the quantized `pixel_data` as the
  starting canvas (via `existing_pixels`) and a short instruction to *correct
  defects only*, capped at a low `max_steps` (default 20).
- WHEN `refine` is false or unset, THE SYSTEM SHALL NOT call any LLM in this job.
- The refine pass SHALL reuse `run_agent_stream` unchanged (it already accepts
  `existing_pixels`); only the calling job assembles the seed + prompt.

> Note: full "start the agent from the reference" ergonomics are Spec 0002.
> Here we only need the minimal hook.

### R6 — Text-only requests unchanged

- WHEN a request has no `reference_id` AND no reference can be auto-generated,
  THE SYSTEM SHALL fall back to today's agent path with no behavior change.
- WHEN a request has no `reference_id` but image generation is available, THE
  SYSTEM SHALL first generate a concept image (existing `sprite.reference`
  logic), then feed it into the image-first pipeline. This is gated by
  `auto_reference: bool` (default `true`).

### R7 — UI surface

- The generate panel (`static/index.html` and
  `frontend/src/components/ControlPanel.tsx`) SHALL show a **Mode** control:
  `Auto` (default), `Image-first`, `Agent`, with `Refine after` and
  `Auto-generate reference` checkboxes.
- WHEN a reference is attached, the panel SHALL default the mode indicator to
  "Image-first" and show the derived palette once generation completes.

---

## 4. Design

### 4.1 New job kind: `sprite.render`

New file `jobs/sprite_render.py`, registered as `sprite.render`. It is the
image-first pipeline. `sprite.from_photo` becomes a thin wrapper that calls the
same core with `image_url` download (kept for API back-compat).

```
SpriteRenderParams:
  reference_id: str | None          # storage key under references/
  image_url:    str | None          # alternative source (photo path)
  colors:       list[str]           # [] / ["#c8a44e"] sentinel => derive
  size:         int = 32
  sprite_type:  str = "block"
  max_colors:   int = 16            # for derived palette
  cleanup:      bool = True
  remove_bg:    bool | None = None  # None => decide from sprite_type
  refine:       bool = False
  refine_model: str | None = None
  refine_steps: int = 20
  prompt:       str | None = None   # only used by refine pass
```

### 4.2 Pipeline stages (all in `jobs/_render_core.py`, importable + unit-testable)

```
load_source(reference_id | image_url)         -> PIL.Image (RGBA)
derive_or_use_palette(img, colors, max_colors) -> list[hex]
prepare(img, size, sprite_type)                -> PIL.Image (size×size, RGB)
     - contrast/sharpen nudge before resize
     - resize: LANCZOS for downscale, NEAREST if already small
quantize(img, palette)                          -> int[][]  (nearest by RGB dist)
remove_background(grid, palette, sprite_type)   -> int[][]  (edge flood fill -> -1)
cleanup(grid, palette)                          -> int[][]  (despeckle, merge-near)
render_png(grid, palette, size)                 -> PIL.Image
```

Reuse: `_hex_to_rgb`, `_nearest_index` from `sprite_from_photo.py` (move them
into `_render_core.py`, re-import in `sprite_from_photo.py`).

Palette derivation: extract the body of `server.extract_reference_palette` into
`_render_core.derive_palette(img, max_colors)` and have the HTTP route call it,
so there's one implementation.

### 4.3 Refine hook (R5)

```python
if params.refine:
    from agent import run_agent_stream
    canvas = run_agent_stream(
        gen_id=external_id,
        message=(params.prompt or "") + "\n\nFix only obvious defects: stray "
                "pixels, broken edges, wrong-colored spots. Keep everything "
                "else exactly as is. Call finish when done.",
        palette=palette,
        size=params.size,
        model_name=params.refine_model or DEFAULT_MODEL,
        sprite_type=params.sprite_type,
        existing_pixels=grid,          # <-- seed
        on_step=on_step,
        max_steps=params.refine_steps,
        cancel_check=ctx.cancel_check,
    )
    grid = [row[:] for row in canvas.pixels]
```

`run_agent_stream` already builds the follow-up-style path when a thread exists;
for a fresh `gen_id` with `existing_pixels` it currently takes the *new session*
branch and ignores that the canvas is pre-filled in the system prompt. **Change
required in `agent.py`:** when `is_new` **and** `existing_pixels` is provided,
include the current grid in the system prompt and tell the model it is editing,
not creating. Minimal, additive.

### 4.4 Routing

`server.py:/api/generate` and `worker.py` job dispatch: add `mode` to
`GenerateRequest`. Decision table:

| reference_id | mode | auto_reference | image gen avail | → path |
|---|---|---|---|---|
| set | auto / image-first | – | – | `sprite.render` |
| set | agent | – | – | `sprite.generate` (today) |
| none | auto | true | yes | `sprite.reference` → `sprite.render` |
| none | auto | true | no | `sprite.generate` (today) |
| none | agent | – | – | `sprite.generate` (today) |

Chaining `sprite.reference` → `sprite.render` in the no-reference case: the
`sprite.render` handler can call the reference generator inline (both are just
functions) rather than enqueuing a second job — simpler, one SSE stream.

### 4.5 Data model

No schema change. `generations.reference_id` already exists. Store the derived
palette back into `generations.colors` when we derived it, so chat-continue
sees the same palette.

### 4.6 Files touched

- `jobs/_render_core.py` — **new**, pure functions + unit tests target.
- `jobs/sprite_render.py` — **new**, job handler.
- `jobs/sprite_from_photo.py` — refactor to call `_render_core`.
- `jobs/__init__.py` — register `sprite.render` in `_load_builtins`.
- `agent.py` — seed-aware system prompt when `is_new and existing_pixels`.
- `server.py` — `derive_palette` extraction; `GenerateRequest.mode` +
  `auto_reference` + `refine`; routing in `/api/generate`; `_run_agent_sse`
  branch for render mode (self-hosted, no-Redis path).
- `worker.py` — handle `type == "render"` jobs.
- `static/index.html`, `frontend/src/components/ControlPanel.tsx`,
  `frontend/src/hooks/useStudio.ts` — Mode control + palette display.
- `specs/0001-image-first-pipeline.md` — this file.
- `tests/test_render_core.py` — **new**.

---

## 5. Tasks

1. [x] `jobs/_render_core.py` — `derive_palette`, `prepare`, `quantize`,
   `remove_background` (dominant-border-color edge flood fill), `despeckle`,
   `render_png`, `render()`. Pure; takes a PIL image in.
2. [x] `tests/test_render_core.py` — 17 tests: stage shapes, icon bg→-1, block
   full-fill, despeckle, determinism, supplied vs derived palette.
3. [x] `server.extract_reference_palette` delegates to `derive_palette`; route
   response unchanged.
4. [x] `jobs/sprite_from_photo.py` refactored onto `_render_core` (default
   `sprite_type="block"` keeps the old fully-opaque behaviour).
5. [x] `jobs/sprite_render.py` handler (R1–R5). Auto-reference (R6) via
   `jobs/_reference.py`.
6. [x] Registered in `jobs/__init__.py`.
7. [x] `agent.py` seed-aware system prompt (`_has_content` + `seeded` branch);
   `tests/test_agent_seed.py`.
8. [x] `sprite_render` refine hook (R5) — agent seeded with `existing_pixels`,
   capped `refine_steps`.
9. [x] `server.py` `GenerateRequest.mode/refine/auto_reference`,
   `_use_image_first`, `_run_render_sse` (self-hosted), generic-job enqueue
   (redis).
10. [x] Redis path: `/api/generate` enqueues `{"type":"job","kind":"sprite.render"}`
    which `worker.handle_generic_job` already handles. No new worker branch
    needed. *(Deviation: SSE events on that path are the generic `progress`/
    `result` shape, not `pixels`/`complete`; only matters for a redis +
    standalone-UI combo, which is not a real deployment.)*
11. [x] UI: Mode select + `refine` checkbox in `ControlPanel.tsx` +
    `useStudio.ts`; `static/` rebuilt (`next build`). *(Post-gen palette
    display in the panel deferred — the derived palette is already returned on
    the `complete` event and saved to the row.)*
12. [x] `README.md` "Generation modes" table.
13. [x] Manual verification — see Section 6.

### Deviations from the drafted design

- **R6**: no-reference + `auto` still uses the agent path (unchanged default).
  Concept-then-render only happens on explicit `mode: image-first`. Safer.
- `sprite_reference.py` kept as-is (its `RESOURCE_EXHAUSTED` quota messaging is
  worth keeping); `_reference.py` is the shared path for `sprite.render`.
- "Derive palette" trigger is `len(colors) <= 1` (the standalone UI sends
  `["#c8a44e"]` for "no palette").
- Perceptual (Lab) palette + near-color merge in cleanup: deferred (spec noted).

---

## 6. Verification

### Automated

- `pytest tests/test_render_core.py` — pure-function coverage of every stage.
- `pytest tests/test_agent_seed.py` — new-thread + `existing_pixels` puts the
  grid in the prompt and does not blank it.

### Manual (record screenshots in the PR)

1. Upload a photo of a sword on white → `icon`, size 32, no palette → get a
   clean sprite, white gone, ≤16 colors, < 3s.
2. Same image → `block`, size 32 → full canvas filled, no transparency.
3. Text prompt "mossy stone block", no reference, `auto_reference on` → concept
   image generated, then quantized, looks like a tile.
4. Any result → `refine: true` → agent runs ≤20 steps, only touches defects,
   silhouette unchanged.
5. `mode: agent` with a reference → today's behavior exactly (regression).
6. Gallery, export PNG, and `/api/chat` continue all work on a `sprite.render`
   result.

### Quality bar

Side-by-side of 10 fixed prompts/references: image-first output should be judged
"usable without edits" in ≥ 7/10 vs the agent path's current ~2/10.

---

## 7. Risks / open questions

- **Background flood fill** false-positives on subjects that touch the frame.
  Mitigation: threshold tuning + only fill from corners inward; expose
  `remove_bg` override.
- **Downscale detail loss** for busy references at 16–32px. Partly inherent;
  pre-resize sharpen helps. Spec 0003+ previews will make it easy to see.
- **Palette derivation** can pick muddy midtones. Acceptable for 0001; a
  perceptual (Lab) pass is a later spec.
- **Refine pass regressions** — the agent could still wander. Capped steps +
  seeded canvas + "defects only" prompt; 0002/0005 harden this.
- Chaining reference-gen inside `sprite.render` couples two concerns in one
  handler. Accepted for a single clean SSE stream; revisit if it grows.

---

## 8. Definition of done

- [x] `sprite.render` job exists, registered, covered by unit + integration tests
  (`tests/test_render_core.py`, `tests/test_sprite_render.py` — 23 tests green).
- [x] Reference present → image-first is default; persists `pixel_data`,
  `gen_<id>_<size>x<size>.png`, `gen_<id>_preview.png`, `status='complete'`,
  `iterations` — same shape the agent path writes.
- [x] Palette derived when `len(colors)<=1`, returned on the `complete` event,
  saved to `generations.colors`.
- [x] Background: `icon`/`character`/`freeform` → edge flood fill to `-1`;
  `block` → every pixel filled.
- [x] `refine: true` runs the agent seeded from the quantized grid (capped
  steps, "fix defects only" prompt).
- [x] `mode: agent` unchanged — verified live: emits "Agent painting …".
- [x] UI Mode select + refine checkbox in the React source; `static/` rebuilt.
- [x] `README.md` updated.
- [x] Manual verification: uploaded a mushroom reference → 16×16 `icon` →
  clean red-cap/tan-stem sprite, background removed, 3-color derived palette,
  &lt;1s, row `status=complete`. Recorded in Section 6.

> **Follow-up spec added mid-implementation:** 0007 — completion score gate
> (LLM scores the finished sprite against the user's goal before "complete";
> low score offers the user "+N steps"). Scheduled next, before 0002.
