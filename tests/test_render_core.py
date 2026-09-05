"""Unit tests for jobs/_render_core.py (spec 0001)."""

from __future__ import annotations

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import _render_core as rc  # noqa: E402


def _solid(size, color, mode="RGBA"):
    return Image.new(mode, (size, size), color)


def test_hex_roundtrip():
    assert rc.hex_to_rgb("#ff8000") == (255, 128, 0)
    assert rc.hex_to_rgb("#f80") == (255, 136, 0)
    assert rc.rgb_to_hex((255, 128, 0)) == "#ff8000"


def test_nearest_index():
    pal = [(0, 0, 0), (255, 255, 255), (255, 0, 0)]
    assert rc.nearest_index((10, 10, 10), pal) == 0
    assert rc.nearest_index((240, 240, 240), pal) == 1
    assert rc.nearest_index((200, 20, 20), pal) == 2


def test_derive_palette_counts_and_order():
    img = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    for y in range(4):
        for x in range(2):
            img.putpixel((x, y), (0, 0, 0, 255))
    pal = rc.derive_palette(img, max_colors=8)
    assert set(pal) == {"#000000", "#ffffff"}
    assert pal[0] == "#000000"  # darkest first


def test_derive_palette_ignores_transparent():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((0, 0), (10, 200, 50, 255))
    assert rc.derive_palette(img) == ["#0ac832"]


def test_quantize_shape_and_transparency():
    img = _solid(8, (255, 0, 0, 255))
    for i in range(8):
        img.putpixel((i, 0), (0, 0, 0, 0))
    grid = rc.quantize(img, ["#ff0000", "#00ff00"])
    assert len(grid) == 8 and len(grid[0]) == 8
    assert grid[0] == [-1] * 8
    assert grid[1] == [0] * 8


def test_quantize_keep_alpha_false_fills():
    img = _solid(4, (255, 0, 0, 0))
    grid = rc.quantize(img, ["#ff0000"], keep_alpha=False)
    assert all(v == 0 for row in grid for v in row)


def test_prepare_size_and_block_flatten():
    img = _solid(64, (0, 128, 255, 0))  # fully transparent blue
    out = rc.prepare(img, 16, "block")
    assert out.size == (16, 16)
    # block => transparency flattened, so alpha is opaque everywhere
    assert all(p[3] == 255 for p in out.convert("RGBA").getdata())


def test_prepare_upscales_with_nearest():
    img = _solid(4, (10, 20, 30, 255))
    out = rc.prepare(img, 16, "icon")
    assert out.size == (16, 16)


def test_remove_background_edge_flood_only():
    # 6x6 red field, green 2x2 block in the centre that is the SAME as a stray
    # green pixel touching no edge -> only the edge-connected red goes away.
    palette = ["#ff0000", "#00ff00"]
    grid = [[0] * 6 for _ in range(6)]
    for y in (2, 3):
        for x in (2, 3):
            grid[y][x] = 1
    out = rc.remove_background(grid, palette)
    assert out[0][0] == -1  # edge red removed
    assert out[2][2] == 1   # interior green untouched
    assert out[3][3] == 1


def test_remove_background_keeps_subject_touching_frame_partially():
    palette = ["#ffffff", "#000000"]
    grid = [[0] * 8 for _ in range(8)]
    for y in range(8):
        grid[y][4] = 1  # vertical black bar down the middle, touches top+bottom
    out = rc.remove_background(grid, palette)
    assert out[3][4] == 1  # bar survives
    assert out[3][0] == -1  # white background gone


def test_despeckle_removes_lone_pixel():
    grid = [[0, 0, 0], [0, 5, 0], [0, 0, 0]]
    out = rc.despeckle(grid)
    assert out[1][1] == 0


def test_despeckle_keeps_real_detail():
    grid = [[0, 1, 0], [1, 1, 0], [0, 0, 0]]  # the 1s form a connected shape
    out = rc.despeckle(grid)
    assert out[0][1] == 1
    assert out[1][0] == 1


def test_render_png_matches_grid():
    grid = [[-1, 0], [1, 0]]
    img = rc.render_png(grid, ["#112233", "#445566"], 2)
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((1, 0))[:3] == (17, 34, 51)
    assert img.getpixel((0, 1))[:3] == (68, 85, 102)


def test_render_block_fills_every_pixel():
    img = _solid(32, (120, 90, 60, 255))
    grid, pal = rc.render(img, size=16, sprite_type="block")
    assert len(grid) == 16
    assert all(v >= 0 for row in grid for v in row)  # no transparency in a tile


def test_render_icon_removes_background():
    img = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
    for y in range(10, 22):
        for x in range(10, 22):
            img.putpixel((x, y), (200, 30, 30, 255))
    grid, pal = rc.render(img, size=16, sprite_type="icon")
    assert grid[0][0] == -1                       # corner background gone
    assert any(v >= 0 for row in grid for v in row)  # subject survives


def test_render_deterministic():
    img = _solid(24, (50, 160, 210, 255))
    for x in range(24):
        img.putpixel((x, x), (240, 240, 240, 255))
    a, _ = rc.render(img, size=16, sprite_type="freeform")
    b, _ = rc.render(img, size=16, sprite_type="freeform")
    assert a == b


def test_render_respects_supplied_palette():
    img = _solid(16, (10, 250, 10, 255))
    grid, pal = rc.render(img, size=8, sprite_type="block", colors=["#000000", "#ffffff"])
    assert pal == ["#000000", "#ffffff"]
    assert set(v for row in grid for v in row) <= {0, 1}
