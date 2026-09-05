"""Persistent reference context — pre_model_hook + view_reference (spec 0004)."""

from __future__ import annotations

import os
import sys

from PIL import Image
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402


def _img_msg(text="preview"):
    return ToolMessage(
        content=[{"type": "text", "text": text},
                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}],
        name="view_canvas", tool_call_id="t",
    )


def _canvas():
    c = agent.Canvas(16, ["#c83232", "#e6d2aa", "#f0f0f0"])
    c.fill_rect(3, 3, 12, 12, 0)
    return c


# ── image pruning (R5) ──

def test_hook_caps_image_parts_and_keeps_text():
    hook = agent._make_context_hook("a red mushroom")
    msgs = [HumanMessage(content=[{"type": "text", "text": "start"},
                                  {"type": "image_url", "image_url": {"url": "data:image/png;base64,REF"}}])]
    for _ in range(6):
        msgs += [AIMessage(content="", tool_calls=[{"name": "view_canvas", "args": {}, "id": "x"}]),
                 _img_msg()]
    out = hook({"messages": msgs})["llm_input_messages"]

    def has_img(m):
        return isinstance(m.content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in m.content)

    imgs = [m for m in out if has_img(m)]
    assert len(imgs) <= agent.MAX_IMAGE_PARTS + 1        # +1 = the exempt message 0
    assert out[0].content[1]["image_url"]["url"].endswith("REF")   # message 0 image kept
    # a stripped message still carries its text
    stripped = [m for m in out if isinstance(m.content, list)
                and any("removed to save context" in p.get("text", "") for p in m.content)]
    assert stripped


def test_hook_reanchors_every_n_when_no_recent_image():
    agent.REANCHOR_EVERY = 3
    hook = agent._make_context_hook("a red mushroom")
    msgs = [HumanMessage(content="go")]
    for _ in range(3):
        msgs += [AIMessage(content="thinking"), ToolMessage(content="Drew", name="fill_rect", tool_call_id="t")]
    out = hook({"messages": msgs})["llm_input_messages"]
    assert isinstance(out[-1], HumanMessage) and "Reminder" in out[-1].content
    agent.REANCHOR_EVERY = 8


def test_hook_skips_reanchor_when_image_recent():
    agent.REANCHOR_EVERY = 3
    hook = agent._make_context_hook("a red mushroom")
    msgs = [HumanMessage(content="go")]
    for _ in range(2):
        msgs += [AIMessage(content="t"), ToolMessage(content="Drew", name="fill_rect", tool_call_id="t")]
    msgs += [AIMessage(content="t"), _img_msg()]        # 3rd AI turn, ends on an image
    out = hook({"messages": msgs})["llm_input_messages"]
    assert not (isinstance(out[-1], HumanMessage) and "Reminder" in str(out[-1].content))
    agent.REANCHOR_EVERY = 8


def test_hook_noop_shape_when_nothing_to_do():
    hook = agent._make_context_hook("subj")
    out = hook({"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]})
    assert "llm_input_messages" in out


# ── view_reference tool (R2) ──

def test_view_reference_multimodal_for_vision():
    c = _canvas()
    ref = Image.new("RGBA", (32, 32), (200, 50, 50, 255))
    tools = {t.name: t for t in agent.make_tools(c, vision=True, tool_images=True, reference_img=ref)}
    out = tools["view_reference"].invoke({})
    assert isinstance(out, list)
    assert sum(1 for p in out if p.get("type") == "image_url") == 2   # reference + current


def test_view_reference_text_when_no_reference():
    c = _canvas()
    tools = {t.name: t for t in agent.make_tools(c, vision=True, tool_images=True)}
    assert "view_reference" not in tools                              # tool omitted entirely


def test_view_reference_text_for_openai_style():
    c = _canvas()
    ref = Image.new("RGBA", (32, 32), (0, 0, 0, 255))
    tools = {t.name: t for t in agent.make_tools(c, vision=True, tool_images=False, reference_img=ref)}
    assert isinstance(tools["view_reference"].invoke({}), str)
