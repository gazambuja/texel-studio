"""Reference-seeded canvas (spec 0002)."""

from __future__ import annotations

import base64
import io
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
from jobs._seed import build_seed, build_seed_from_b64  # noqa: E402


def _mushroom(n=48):
    img = Image.new("RGBA", (n, n), (240, 240, 240, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([10, 8, n - 10, n - 16], fill=(200, 50, 50, 255))
    d.rectangle([n // 2 - 5, n - 18, n // 2 + 5, n - 4], fill=(230, 210, 170, 255))
    return img


# ── seed builder ──

def test_build_seed_icon_has_transparent_background_and_mask():
    grid, mask = build_seed(_mushroom(), 16, "icon", ["#c83232", "#e6d2aa", "#f0f0f0"])
    assert len(grid) == 16 and len(grid[0]) == 16
    assert grid[0][0] == -1                              # bg removed
    assert (0, 0) not in mask
    assert mask                                          # subject present
    assert all(grid[y][x] >= 0 for (x, y) in mask)       # mask == opaque cells


def test_build_seed_block_fills_everything():
    grid, mask = build_seed(_mushroom(), 16, "block", ["#000000", "#ffffff"])
    assert all(v >= 0 for row in grid for v in row)
    assert len(mask) == 16 * 16


def test_build_seed_from_b64_roundtrip():
    buf = io.BytesIO()
    _mushroom().save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    grid, mask = build_seed_from_b64(b64, 12, "icon", ["#c83232", "#e6d2aa", "#f0f0f0"])
    assert len(grid) == 12 and mask


# ── Canvas lock enforcement ──

def _locked_canvas():
    seed = [[0, -1, -1], [0, 0, -1], [-1, -1, -1]]
    mask = {(0, 0), (0, 1), (1, 1)}
    return agent.Canvas(3, ["#000000", "#ffffff"], [r[:] for r in seed], silhouette=mask, locked=True)


def test_locked_blocks_erasing_silhouette():
    c = _locked_canvas()
    assert "Skipped" in c.set_pixel(0, 0, -1)
    assert c.get_pixel(0, 0) == 0
    assert c.lock_hits == 1


def test_locked_blocks_extending_silhouette():
    c = _locked_canvas()
    assert "Skipped" in c.set_pixel(2, 2, 1)
    assert c.get_pixel(2, 2) == -1


def test_locked_allows_recolor_inside():
    c = _locked_canvas()
    assert "Set" in c.set_pixel(0, 0, 1)
    assert c.get_pixel(0, 0) == 1


def test_locked_fill_rect_only_touches_silhouette():
    c = _locked_canvas()
    c.fill_rect(0, 0, 2, 2, 1)
    opaque = {(x, y) for y in range(3) for x in range(3) if c.get_pixel(x, y) >= 0}
    assert opaque == {(0, 0), (0, 1), (1, 1)}


def test_soft_mode_allows_reshaping():
    seed = [[0, -1, -1], [0, 0, -1], [-1, -1, -1]]
    c = agent.Canvas(3, ["#000000", "#ffffff"], seed, silhouette={(0, 0), (0, 1), (1, 1)}, locked=False)
    assert "Set" in c.set_pixel(2, 2, 0)
    assert c.get_pixel(2, 2) == 0
    assert c.lock_hits == 0


def test_unlocked_no_silhouette_is_unchanged_behaviour():
    c = agent.Canvas(4, ["#000000", "#ffffff"])
    assert "Filled rect" in c.fill_rect(0, 0, 3, 3, 0)
    assert all(v == 0 for row in c.pixels for v in row)


# ── run_agent_stream seeds the canvas before the agent runs ──

class _NoopAgent:
    def stream(self, *a, **k):
        return iter(())


def _patch_agent(monkeypatch):
    monkeypatch.setattr(agent, "thread_exists", lambda gid: False)
    monkeypatch.setattr(agent, "_get_llm", lambda *a, **k: object())
    monkeypatch.setattr(agent, "get_checkpointer", lambda: None)
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **k: _NoopAgent())


def _ref_b64():
    buf = io.BytesIO()
    _mushroom().save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_run_agent_stream_seeds_canvas_soft(monkeypatch):
    _patch_agent(monkeypatch)
    canvas = agent.run_agent_stream(
        gen_id="seed_soft_1", message="x", palette=["#c83232", "#e6d2aa", "#f0f0f0"],
        size=16, model_name="fake", sprite_type="icon",
        reference_b64=_ref_b64(), seed_mode="soft",
    )
    assert any(v != -1 for row in canvas.pixels for v in row)   # not blank
    assert canvas.silhouette and not canvas.locked
    assert canvas.seed_pixels is not None


def test_run_agent_stream_locked_sets_flag(monkeypatch):
    _patch_agent(monkeypatch)
    canvas = agent.run_agent_stream(
        gen_id="seed_locked_1", message="x", palette=["#c83232", "#e6d2aa", "#f0f0f0"],
        size=16, model_name="fake", sprite_type="icon",
        reference_b64=_ref_b64(), seed_mode="locked",
    )
    assert canvas.locked and canvas.silhouette


def test_run_agent_stream_off_is_blank(monkeypatch):
    _patch_agent(monkeypatch)
    canvas = agent.run_agent_stream(
        gen_id="seed_off_1", message="x", palette=["#c83232", "#e6d2aa"],
        size=16, model_name="fake", sprite_type="icon",
        reference_b64=_ref_b64(), seed_mode="off",
    )
    assert all(v == -1 for row in canvas.pixels for v in row)
    assert canvas.silhouette is None and canvas.seed_pixels is None
