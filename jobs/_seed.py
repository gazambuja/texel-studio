"""Reference → seed grid + silhouette mask (spec 0002).

The agent path, when given a reference, starts from a quantized underlay of that
reference instead of a blank canvas. This is exactly the `_render_core` pipeline
minus the PNG render — factored here so `run_agent_stream` can build a seed from
a base64 reference without importing image code directly.
"""

from __future__ import annotations

import base64
import io

from ._render_core import Grid, prepare, quantize, remove_background, _BG_REMOVAL_TYPES


def build_seed(img, size: int, sprite_type: str, palette: list[str]) -> tuple[Grid, set]:
    """Return (seed_grid, silhouette) for a PIL image.

    `silhouette` is the set of (x, y) that are opaque in the seed — used for
    locked-mode enforcement in `agent.Canvas`.
    """
    prepared = prepare(img, size, sprite_type)
    grid = quantize(prepared, palette, keep_alpha=(sprite_type != "block"))
    if sprite_type in _BG_REMOVAL_TYPES:
        grid = remove_background(grid, palette)
    silhouette = {
        (x, y)
        for y, row in enumerate(grid)
        for x, v in enumerate(row)
        if v >= 0
    }
    return grid, silhouette


def build_seed_from_b64(
    reference_b64: str, size: int, sprite_type: str, palette: list[str]
) -> tuple[Grid, set]:
    from PIL import Image

    raw = base64.b64decode(reference_b64)
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    return build_seed(img, size, sprite_type, palette)
