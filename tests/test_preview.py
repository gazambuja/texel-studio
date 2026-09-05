"""Agent preview triptych + pre_model_hook (spec 0003)."""

from __future__ import annotations

import os
import sys

from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
import preview  # noqa: E402


def _canvas(n=16):
    c = agent.Canvas(n, ["#c83232", "#e6d2aa", "#f0f0f0"], None)
    c.fill_rect(2, 2, n - 3, n - 3, 0)
    return c


def test_scale_formula_clamped():
    assert preview._scale_for(32) * 32 == 320       # 32px -> ~320px panel
    for n in (8, 16, 32, 64, 128):
        px = n * preview._scale_for(n)
        assert preview.PANEL_MIN <= px <= preview.PANEL_MAX


def test_triptych_has_three_panels_with_reference():
    c = _canvas(16)
    ref = Image.new("RGBA", (40, 40), (200, 50, 50, 255))
    img = preview.build_preview_image(c, ref)
    two = preview.build_preview_image(c, None)
    # 3 panels vs 2 -> wider with a reference
    assert img.width > two.width
    assert img.mode == "RGB"
    assert img.height == two.height


def test_preview_text_ascii_only_for_small_canvas():
    assert "0123" in preview.build_preview_text(_canvas(16))       # ruler present
    big = preview.build_preview_text(_canvas(64))
    assert "attached preview image" in big
    assert "\n 0 " not in big and "\n 1 " not in big               # no ascii rows


def test_preview_text_notes_locked():
    c = agent.Canvas(8, ["#000000", "#ffffff"], None, silhouette={(0, 0)}, locked=True)
    assert "LOCKED" in preview.build_preview_text(c)


# ── view_canvas tool return ──

def test_view_canvas_returns_multimodal_for_vision():
    c = _canvas(16)
    tools = {t.name: t for t in agent.make_tools(c, vision=True, tool_images=True)}
    out = tools["view_canvas"].invoke({})
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_view_canvas_text_only_when_tool_images_off():
    c = _canvas(16)
    tools = {t.name: t for t in agent.make_tools(c, vision=True, tool_images=False)}
    out = tools["view_canvas"].invoke({})
    assert isinstance(out, str) and "LEGEND" in out


def test_view_canvas_text_only_for_nonvision():
    c = _canvas(16)
    tools = {t.name: t for t in agent.make_tools(c, vision=False)}
    assert isinstance(tools["view_canvas"].invoke({}), str)


def test_supports_tool_images_by_provider():
    assert agent._supports_tool_images("gemini-3-flash-preview") is True
    assert agent._supports_tool_images("gpt-5.4-mini") is False
