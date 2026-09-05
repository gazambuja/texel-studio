"""Agent-facing canvas preview (spec 0003).

`view_canvas` used to embed a hard-coded 64x64 base64 PNG inside its text result.
Small, unlabelled, and buried in a tool string where models barely look at it.

This module builds:
  * `build_preview_image(canvas, reference_img)` — a labelled triptych
    (reference | canvas | canvas+coordinate-grid), each NEAREST-upscaled to a
    legible size, with agent edits tinted vs the seed underlay.
  * `build_preview_text(canvas)` — legend + spatial summary, plus the ASCII
    grid only for small canvases where it still helps.

`run_agent_stream` feeds the image to the model as a real image message via a
`pre_model_hook`, so it works the same across Gemini / OpenAI / Ollama.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

# ── config (env-overridable for experimentation) ──
PANEL_TARGET = int(os.getenv("PREVIEW_PANEL_PX", "320"))   # px for a 32-wide canvas
PANEL_MIN = int(os.getenv("PREVIEW_PANEL_MIN", "192"))
PANEL_MAX = int(os.getenv("PREVIEW_PANEL_MAX", "512"))
ASCII_MAX = int(os.getenv("PREVIEW_ASCII_MAX", "32"))       # ASCII grid only at/below this
LABEL_H = 16
MARGIN = 22                                                 # axis-label gutter on the grid panel

_EDIT_TINT = (120, 200, 255, 90)


def _scale_for(canvas_size: int) -> int:
    """Integer NEAREST scale that lands closest to PANEL_TARGET, clamped."""
    s = max(1, round(PANEL_TARGET / canvas_size))
    while canvas_size * s > PANEL_MAX and s > 1:
        s -= 1
    while canvas_size * s < PANEL_MIN:
        s += 1
    return s


def _checker(size_px: int, cell: int) -> Image.Image:
    img = Image.new("RGBA", (size_px, size_px), (26, 24, 18, 255))
    d = ImageDraw.Draw(img)
    for y in range(0, size_px, cell):
        for x in range(0, size_px, cell):
            if ((x // cell) + (y // cell)) % 2:
                d.rectangle([x, y, x + cell, y + cell], fill=(19, 17, 12, 255))
    return img


def _edits(canvas) -> set:
    seed = getattr(canvas, "seed_pixels", None)
    if not seed:
        return set()
    return {
        (x, y)
        for y, row in enumerate(canvas.pixels)
        for x, v in enumerate(row)
        if y < len(seed) and x < len(seed[y]) and seed[y][x] != v
    }


def _sprite_panel(canvas, scale: int, edits: set, grid_overlay: bool) -> Image.Image:
    n = canvas.size
    px = n * scale
    if grid_overlay:
        panel = Image.new("RGBA", (px + MARGIN, px + MARGIN), (10, 10, 10, 255))
        ox = oy = MARGIN
    else:
        panel = _checker(px, max(4, scale))
        ox = oy = 0

    sprite = canvas.to_image().resize((px, px), Image.NEAREST)
    panel.alpha_composite(sprite, (ox, oy))

    d = ImageDraw.Draw(panel)
    if edits:
        tint = Image.new("RGBA", (scale, scale), _EDIT_TINT)
        for (x, y) in edits:
            panel.alpha_composite(tint, (ox + x * scale, oy + y * scale))

    if grid_overlay:
        for i in range(n + 1):
            strong = i % 8 == 0
            col = (255, 255, 255, 70) if strong else (255, 255, 255, 22)
            d.line([(ox + i * scale, oy), (ox + i * scale, oy + px)], fill=col, width=1)
            d.line([(ox, oy + i * scale), (ox + px, oy + i * scale)], fill=col, width=1)
        step = 8 if n > 16 else 4 if n > 8 else 2
        for i in range(0, n, step):
            d.text((ox + i * scale + 1, 4), str(i), fill=(200, 200, 200, 220))
            d.text((3, oy + i * scale + 1), str(i), fill=(200, 200, 200, 220))
    return panel


def build_preview_image(canvas, reference_img: Image.Image | None = None):
    """Labelled triptych: [reference] | canvas | canvas+grid."""
    scale = _scale_for(canvas.size)
    edits = _edits(canvas)

    panels: list[tuple[str, Image.Image]] = []
    if reference_img is not None:
        px = canvas.size * scale
        ref = reference_img.convert("RGBA").resize((px, px), Image.NEAREST)
        panels.append(("REFERENCE", ref))
    panels.append(("CURRENT", _sprite_panel(canvas, scale, edits, grid_overlay=False)))
    panels.append(("GRID (x→ across, y↓ down)", _sprite_panel(canvas, scale, edits, grid_overlay=True)))

    gap = 12
    ph = max(p.height for _, p in panels)
    total_w = sum(p.width for _, p in panels) + gap * (len(panels) - 1)
    out = Image.new("RGBA", (total_w, ph + LABEL_H), (12, 12, 12, 255))
    d = ImageDraw.Draw(out)
    x = 0
    for label, p in panels:
        d.text((x + 2, 3), label, fill=(180, 180, 180, 255))
        out.alpha_composite(p, (x, LABEL_H))
        x += p.width + gap
    return out.convert("RGB")


def build_preview_text(canvas) -> str:
    color_counts: dict[int, int] = {}
    for row in canvas.pixels:
        for v in row:
            color_counts[v] = color_counts.get(v, 0) + 1

    legend = []
    for idx, count in sorted(color_counts.items(), key=lambda kv: -kv[1]):
        if idx == -1:
            legend.append(f". = transparent: {count}px")
        elif 0 <= idx < len(canvas.palette):
            char = str(idx) if idx < 10 else chr(ord("A") + idx - 10)
            legend.append(f"{char} = {idx}({canvas.palette[idx]}): {count}px")

    total = sum(c for i, c in color_counts.items() if i >= 0)
    half = canvas.size // 2
    spatial = (
        f"TOP-LEFT: {canvas.region_summary(0, 0, half-1, half-1)} | "
        f"TOP-RIGHT: {canvas.region_summary(0, half, half-1, canvas.size-1)} | "
        f"BOTTOM-LEFT: {canvas.region_summary(half, 0, canvas.size-1, half-1)} | "
        f"BOTTOM-RIGHT: {canvas.region_summary(half, half, canvas.size-1, canvas.size-1)}"
    )

    parts = []
    if canvas.size <= ASCII_MAX:
        parts.append(
            "GRID — top row of digits = x (column), left column of digits = y (row); "
            "each char is a palette index, '.' = transparent:"
        )
        parts.append(canvas.to_visual_grid())
    else:
        parts.append(
            f"(canvas is {canvas.size}px — read the attached preview image; "
            "rows are y top→bottom, columns are x left→right)"
        )
    parts.append(f"LEGEND: {', '.join(legend[:12])}")
    parts.append(f"Filled: {total}/{canvas.size * canvas.size}px")
    parts.append(f"LAYOUT: {spatial}")
    if getattr(canvas, "locked", False):
        parts.append("NOTE: the silhouette is LOCKED — outline changes are ignored.")
    return "\n".join(parts)
