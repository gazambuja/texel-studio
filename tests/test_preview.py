"""Agent preview triptych + pre_model_hook (spec 0003)."""

from __future__ import annotations

import os
import sys

from PIL import Image
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

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


# ── pre_model_hook ──

def test_hook_passthrough_when_last_not_view_canvas():
    hook = agent._make_preview_hook(_canvas(16), None)
    state = {"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]}
    out = hook(state)
    assert "llm_input_messages" in out and "messages" not in out


def test_hook_injects_image_after_view_canvas():
    hook = agent._make_preview_hook(_canvas(16), None)
    tm = ToolMessage(content="legend...", name="view_canvas", tool_call_id="c1")
    out = hook({"messages": [HumanMessage(content="draw"), tm]})
    assert "messages" in out
    injected = out["messages"][-1]
    assert isinstance(injected, HumanMessage)
    kinds = [p.get("type") for p in injected.content]
    assert "image_url" in kinds
    assert injected.content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_hook_removes_previous_preview():
    hook = agent._make_preview_hook(_canvas(16), None)
    old = HumanMessage(
        id="old-preview",
        content=[{"type": "text", "text": agent._PREVIEW_MARKER + " old"},
                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}],
    )
    tm = ToolMessage(content="legend", name="view_canvas", tool_call_id="c2")
    out = hook({"messages": [old, AIMessage(content=""), tm]})
    assert any(isinstance(m, RemoveMessage) and m.id == "old-preview" for m in out["messages"])
    assert any(isinstance(m, HumanMessage) for m in out["messages"])
