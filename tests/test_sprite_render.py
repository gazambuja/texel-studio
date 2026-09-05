"""Integration test for the sprite.render job handler (spec 0001)."""

from __future__ import annotations

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from jobs import JobContext, get_handler, list_kinds  # noqa: E402
from jobs.sprite_render import SpriteRenderParams  # noqa: E402


def _write_reference(rid: str) -> None:
    img = Image.new("RGBA", (48, 48), (255, 255, 255, 255))
    for y in range(14, 34):
        for x in range(14, 34):
            img.putpixel((x, y), (200, 40, 40, 255))
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    storage.save_file(f"references/{rid}", buf.getvalue())


def test_render_registered():
    assert "sprite.render" in list_kinds()


def test_render_from_reference_icon():
    rid = "ref_test_icon.png"
    _write_reference(rid)
    handler = get_handler("sprite.render")()
    params = SpriteRenderParams(reference_id=rid, size=16, sprite_type="icon", auto_reference=False)
    ctx = JobContext(job_id="test_icon_1", external_id="test_icon_1")

    events = list(handler.run(params, ctx))
    names = [e.name for e in events]
    assert "result" in names
    assert "error" not in names

    result = next(e for e in events if e.name == "result")
    grid = result.data["pixel_data"]
    assert len(grid) == 16 and len(grid[0]) == 16
    assert grid[0][0] == -1                       # background removed
    assert any(v >= 0 for row in grid for v in row)
    assert result.data["colors"]                  # palette derived
    assert result.data["image_path"] == "gen_test_icon_1_16x16.png"
    assert storage.file_exists("output/gen_test_icon_1_16x16.png")
    assert storage.file_exists("output/gen_test_icon_1_preview.png")


def test_render_block_fills_and_uses_supplied_palette():
    rid = "ref_test_block.png"
    _write_reference(rid)
    handler = get_handler("sprite.render")()
    params = SpriteRenderParams(
        reference_id=rid, size=16, sprite_type="block",
        colors=["#000000", "#ffffff"], auto_reference=False,
    )
    ctx = JobContext(job_id="test_block_1", external_id="test_block_1")
    events = list(handler.run(params, ctx))
    result = next(e for e in events if e.name == "result")
    grid = result.data["pixel_data"]
    assert all(v in (0, 1) for row in grid for v in row)  # full fill, supplied palette
    assert result.data["colors"] == ["#000000", "#ffffff"]


def test_render_requires_a_source():
    handler = get_handler("sprite.render")()
    params = SpriteRenderParams(size=16, auto_reference=False)
    ctx = JobContext(job_id="test_nosrc", external_id="test_nosrc")
    events = list(handler.run(params, ctx))
    assert any(e.name == "error" for e in events)
