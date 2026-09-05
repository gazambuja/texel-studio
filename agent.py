"""
Texel Studio Agent — LangGraph-based pixel art agent with canvas tools.

The agent gets a canvas, a palette, and tools to draw on it.
It thinks between each action, building up the sprite incrementally.
Supports continuation — send follow-up messages to the same agent thread.
"""

import json
import base64
import io
import uuid
from typing import Any

from PIL import Image
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


# ── Checkpointer factory ──
#
# In self-hosted mode (no REDIS_URL) we keep the existing MemorySaver — sessions
# live in worker memory, fine for a single process.
#
# In Redis-backed mode the LangGraph thread state is persisted to Redis Stack
# via langgraph-checkpoint-redis, so any worker can resume any thread by ID.
# That kills the worker-affinity routing the old `_sessions` dict required.
#
# RedisSaver.from_conn_string is a context manager. We hold a single open
# instance for the process so it can be reused across run_agent_stream calls.

_redis_checkpointer = None
_redis_checkpointer_ctx = None


def get_checkpointer():
    """Return a process-wide checkpointer. Redis if REDIS_URL set, else Memory."""
    global _redis_checkpointer, _redis_checkpointer_ctx
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        # Single in-memory saver per process is fine for the standalone engine.
        if _redis_checkpointer is None:
            _redis_checkpointer = MemorySaver()
        return _redis_checkpointer
    if _redis_checkpointer is None:
        from langgraph.checkpoint.redis import RedisSaver
        _redis_checkpointer_ctx = RedisSaver.from_conn_string(redis_url)
        _redis_checkpointer = _redis_checkpointer_ctx.__enter__()
        _redis_checkpointer.setup()
    return _redis_checkpointer


def _thread_id_for(gen_id) -> str:
    """Deterministic thread ID per generation. Lets any worker resume any thread."""
    return f"job_{gen_id}"


def _has_content(pixels) -> bool:
    """True if a pixel grid has at least one non-transparent cell."""
    return bool(pixels) and any(v != -1 for row in pixels for v in row)

# ── PostHog LLM Analytics (optional) ──

_posthog_client = None

def _get_posthog_callback(distinct_id: str | None = None, trace_id: str | None = None):
    """Returns a PostHog CallbackHandler if POSTHOG_API_KEY is set, else None."""
    global _posthog_client
    api_key = os.getenv("POSTHOG_API_KEY")
    if not api_key:
        return None
    try:
        if _posthog_client is None:
            from posthog import Posthog
            _posthog_client = Posthog(api_key, host=os.getenv("POSTHOG_HOST", "https://us.i.posthog.com"))
        from posthog.ai.langchain import CallbackHandler
        return CallbackHandler(
            client=_posthog_client,
            distinct_id=distinct_id or "anonymous",
            trace_id=trace_id,
        )
    except Exception:
        return None


# ── Canvas State ──

class Canvas:
    def __init__(self, size: int, palette: list[str], pixels: list[list[int]] | None = None,
                 silhouette: set | None = None, locked: bool = False):
        self.size = size
        self.palette = palette
        self.pixels = pixels if pixels else [[-1] * size for _ in range(size)]
        # spec 0002 — reference-seeded canvas.
        # `silhouette` is the set of (x, y) that were opaque in the seed. When
        # `locked`, drawing may not flip a pixel across that boundary
        # (opaque<->transparent); colors inside the silhouette stay editable.
        self.silhouette = silhouette
        self.locked = locked
        self.lock_hits = 0
        self.seed_pixels: list[list[int]] | None = None

    def _put(self, x: int, y: int, color: int) -> bool:
        """Single write choke point. Returns True if the pixel was written."""
        if not (0 <= x < self.size and 0 <= y < self.size):
            return False
        if self.locked and self.silhouette is not None:
            inside = (x, y) in self.silhouette
            becomes_transparent = color == -1
            if (inside and becomes_transparent) or (not inside and not becomes_transparent):
                self.lock_hits += 1
                return False
        self.pixels[y][x] = color
        return True

    def set_pixel(self, x: int, y: int, color: int) -> str:
        if not (0 <= x < self.size and 0 <= y < self.size):
            return f"Error: ({x},{y}) out of bounds (0-{self.size-1})"
        if color < -1 or color >= len(self.palette):
            return f"Error: color index {color} invalid (use -1 to {len(self.palette)-1})"
        if not self._put(x, y, color):
            return f"Skipped ({x},{y}): locked silhouette pixel"
        return f"Set ({x},{y}) to {color}"

    def get_pixel(self, x: int, y: int) -> int:
        if 0 <= x < self.size and 0 <= y < self.size:
            return self.pixels[y][x]
        return -1

    def fill_rect(self, x1: int, y1: int, x2: int, y2: int, color: int) -> str:
        if color < -1 or color >= len(self.palette):
            return f"Error: color index {color} invalid"
        count = 0
        for y in range(max(0, y1), min(self.size, y2 + 1)):
            for x in range(max(0, x1), min(self.size, x2 + 1)):
                if self._put(x, y, color):
                    count += 1
        return f"Filled rect ({x1},{y1})-({x2},{y2}) with {color}, {count} pixels"

    def draw_line(self, x1: int, y1: int, x2: int, y2: int, color: int) -> str:
        if color < -1 or color >= len(self.palette):
            return f"Error: color index {color} invalid"
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy
        count = 0
        cx, cy = x1, y1
        while True:
            if self._put(cx, cy, color):
                count += 1
            if cx == x2 and cy == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                cx += sx
            if e2 < dx:
                err += dx
                cy += sy
        return f"Drew line, {count} pixels"

    def fill_row(self, y: int, x_start: int, x_end: int, color: int) -> str:
        if color < -1 or color >= len(self.palette):
            return f"Error: color index {color} invalid"
        count = 0
        for x in range(max(0, x_start), min(self.size, x_end + 1)):
            if self._put(x, y, color):
                count += 1
        return f"Filled row y={y}, {count} pixels"

    def fill_column(self, x: int, y_start: int, y_end: int, color: int) -> str:
        if color < -1 or color >= len(self.palette):
            return f"Error: color index {color} invalid"
        count = 0
        for y in range(max(0, y_start), min(self.size, y_end + 1)):
            if self._put(x, y, color):
                count += 1
        return f"Filled column x={x}, {count} pixels"

    def draw_rotated_rect(self, cx: int, cy: int, w: int, h: int, angle_deg: float, color: int) -> int:
        """Draw a filled rotated rectangle. cx,cy = center, w,h = full width/height, angle_deg = rotation."""
        import math
        rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        hw, hh = w / 2, h / 2
        # Check bounding box
        max_r = math.ceil(math.sqrt(hw * hw + hh * hh)) + 1
        count = 0
        for py in range(max(0, cy - max_r), min(self.size, cy + max_r + 1)):
            for px in range(max(0, cx - max_r), min(self.size, cx + max_r + 1)):
                # Rotate point into rect's local space
                dx = px - cx
                dy = py - cy
                lx = dx * cos_a + dy * sin_a
                ly = -dx * sin_a + dy * cos_a
                if abs(lx) <= hw and abs(ly) <= hh:
                    if self._put(px, py, color):
                        count += 1
        return count

    def to_image(self) -> Image.Image:
        img = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        for y, row in enumerate(self.pixels):
            for x, idx in enumerate(row):
                if 0 <= idx < len(self.palette):
                    h = self.palette[idx]
                    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
                    img.putpixel((x, y), (r, g, b, 255))
        return img

    def to_grid_string(self) -> str:
        header = "    " + " ".join(f"{x:>3}" for x in range(self.size))
        rows = [f"{y:>3} " + " ".join(f"{v:>3}" for v in row) for y, row in enumerate(self.pixels)]
        return header + "\n" + "\n".join(rows)

    def to_visual_grid(self) -> str:
        """Compact visual grid using single-char symbols. Much easier for small LLMs to parse."""
        # Map palette indices to readable chars: 0=0, 1=1, ..., 9=9, 10=A, 11=B, ..., -1=.
        def _char(v: int) -> str:
            if v < 0: return "."
            if v < 10: return str(v)
            if v < 36: return chr(ord("A") + v - 10)
            return "#"

        # Column ruler
        if self.size <= 16:
            ruler = "   " + "".join(f"{x:X}" for x in range(self.size))
        else:
            # Two-line ruler for 32+
            tens = "   " + "".join(str(x // 10) if x >= 10 else " " for x in range(self.size))
            ones = "   " + "".join(f"{x % 10}" for x in range(self.size))
            ruler = tens + "\n" + ones

        rows = []
        for y, row in enumerate(self.pixels):
            label = f"{y:>2} " if self.size <= 16 else f"{y:>3}"
            rows.append(label + "".join(_char(v) for v in row))

        return ruler + "\n" + "\n".join(rows)

    def region_summary(self, y1: int, x1: int, y2: int, x2: int) -> str:
        """Describe what's in a rectangular region — helps the model understand spatial layout."""
        counts: dict[int, int] = {}
        for y in range(max(0, y1), min(self.size, y2 + 1)):
            for x in range(max(0, x1), min(self.size, x2 + 1)):
                v = self.pixels[y][x]
                counts[v] = counts.get(v, 0) + 1
        total = sum(counts.values())
        if total == 0:
            return "empty"
        parts = []
        for idx, c in sorted(counts.items(), key=lambda x: -x[1]):
            pct = c * 100 // total
            if pct < 5:
                continue
            if idx < 0:
                parts.append(f"empty:{pct}%")
            else:
                parts.append(f"{idx}:{pct}%")
        return " ".join(parts)

    # ── Shape drawing ──

    def draw_circle(self, cx: int, cy: int, radius: int, color: int, fill: bool = True) -> int:
        count = 0
        for y in range(max(0, cy - radius), min(self.size, cy + radius + 1)):
            for x in range(max(0, cx - radius), min(self.size, cx + radius + 1)):
                dx, dy = x - cx, y - cy
                dist_sq = dx * dx + dy * dy
                r_sq = radius * radius
                hit = dist_sq <= r_sq if fill else abs(dist_sq - r_sq) <= radius * 2
                if hit and self._put(x, y, color):
                    count += 1
        return count

    def draw_ellipse(self, cx: int, cy: int, rx: int, ry: int, color: int, fill: bool = True) -> int:
        count = 0
        for y in range(max(0, cy - ry), min(self.size, cy + ry + 1)):
            for x in range(max(0, cx - rx), min(self.size, cx + rx + 1)):
                dx, dy = (x - cx) / max(rx, 1), (y - cy) / max(ry, 1)
                dist = dx * dx + dy * dy
                hit = dist <= 1.0 if fill else abs(dist - 1.0) <= 0.3
                if hit and self._put(x, y, color):
                    count += 1
        return count

    def draw_triangle(self, x1: int, y1: int, x2: int, y2: int, x3: int, y3: int, color: int, fill: bool = True) -> int:
        def sign(px, py, ax, ay, bx, by):
            return (px - bx) * (ay - by) - (ax - bx) * (py - by)

        min_x = max(0, min(x1, x2, x3))
        max_x = min(self.size - 1, max(x1, x2, x3))
        min_y = max(0, min(y1, y2, y3))
        max_y = min(self.size - 1, max(y1, y2, y3))

        count = 0
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                d1 = sign(x, y, x1, y1, x2, y2)
                d2 = sign(x, y, x2, y2, x3, y3)
                d3 = sign(x, y, x3, y3, x1, y1)
                has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
                has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
                if not (has_neg and has_pos) and self._put(x, y, color):
                    count += 1
        return count

    # ── Noise filling ──

    @staticmethod
    def _hash_noise(x: int, y: int, seed: int) -> float:
        n = x * 374761393 + y * 668265263 + seed * 1274126177
        n = ((n ^ (n >> 13)) * 1274126177) & 0x7fffffff
        n = n ^ (n >> 16)
        return (n & 0x7fffffff) / 0x7fffffff

    def fill_noise(self, x1: int, y1: int, x2: int, y2: int,
                   colors: list[int], seed: int = 42, scale: float = 1.0) -> int:
        """Simple value noise — distributes colors randomly based on noise."""
        count = 0
        n_colors = len(colors)
        if n_colors == 0:
            return 0
        for y in range(max(0, y1), min(self.size, y2 + 1)):
            for x in range(max(0, x1), min(self.size, x2 + 1)):
                n = self._hash_noise(int(x * scale), int(y * scale), seed)
                idx = int(n * n_colors) % n_colors
                if self._put(x, y, colors[idx]):
                    count += 1
        return count

    def fill_voronoi(self, x1: int, y1: int, x2: int, y2: int,
                     colors: list[int], num_points: int = 8, seed: int = 42) -> int:
        """Voronoi noise — creates cell-like patterns with given colors."""
        import math
        w = x2 - x1 + 1
        h = y2 - y1 + 1
        # Generate random seed points
        points = []
        for i in range(num_points):
            px = x1 + int(self._hash_noise(i, 0, seed) * w)
            py = y1 + int(self._hash_noise(0, i, seed + 99) * h)
            points.append((px, py, colors[i % len(colors)]))

        count = 0
        for y in range(max(0, y1), min(self.size, y2 + 1)):
            for x in range(max(0, x1), min(self.size, x2 + 1)):
                best_dist = float('inf')
                best_color = colors[0]
                for px, py, pc in points:
                    d = (x - px) ** 2 + (y - py) ** 2
                    if d < best_dist:
                        best_dist = d
                        best_color = pc
                if self._put(x, y, best_color):
                    count += 1
        return count

    def fill_noise_circle(self, cx: int, cy: int, radius: int,
                          colors: list[int], seed: int = 42) -> int:
        """Fill a circular area with noise-distributed colors."""
        count = 0
        n_colors = len(colors)
        if n_colors == 0:
            return 0
        for y in range(max(0, cy - radius), min(self.size, cy + radius + 1)):
            for x in range(max(0, cx - radius), min(self.size, cx + radius + 1)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                    n = self._hash_noise(x, y, seed)
                    if self._put(x, y, colors[int(n * n_colors) % n_colors]):
                        count += 1
        return count

    def to_image_b64(self, scale: int = 512) -> str:
        img = self.to_image().resize((scale, scale), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()


# ── Tool factory ──

def _is_vision_model(model_name: str) -> bool:
    """Check if a model supports image input (base64 previews)."""
    # Ollama local models generally don't support vision
    ollama = set(m.strip() for m in os.getenv("OLLAMA_MODELS", "").split(",") if m.strip())
    if model_name in ollama:
        return False
    # Most cloud models support vision
    return True


def _supports_tool_images(model_name: str) -> bool:
    """True if the provider accepts image content inside a tool result message.

    Gemini (direct + Vertex) and most local vision models do; OpenAI's function
    role is text-only, so those fall back to the text-only preview.
    """
    return not model_name.startswith(OPENAI_MODEL_PREFIXES) and model_name not in OPENAI_MODELS_ENV


def _preview_image_block(canvas, reference_img) -> dict | None:
    import base64 as _b64
    import io as _io
    try:
        from preview import build_preview_image
        buf = _io.BytesIO()
        build_preview_image(canvas, reference_img).save(buf, format="PNG")
        url = "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()
        return {"type": "image_url", "image_url": {"url": url}}
    except Exception:
        return None


def _image_block_from(img, target_px: int = 320) -> dict | None:
    import base64 as _b64
    import io as _io
    try:
        w = img.width or 1
        scale = max(1, round(target_px / w))
        big = img.convert("RGBA").resize((w * scale, (img.height or 1) * scale), Image.NEAREST)
        buf = _io.BytesIO()
        big.save(buf, format="PNG")
        return {"type": "image_url",
                "image_url": {"url": "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()}}
    except Exception:
        return None


# ── spec 0004 — persistent reference context ──

REANCHOR_EVERY = int(os.getenv("REANCHOR_EVERY", "8"))
MAX_IMAGE_PARTS = int(os.getenv("MAX_IMAGE_PARTS", "3"))


def _msg_has_image(msg) -> bool:
    c = getattr(msg, "content", None)
    return isinstance(c, list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in c
    )


def _strip_images(msg):
    kept = [p for p in msg.content
            if not (isinstance(p, dict) and p.get("type") == "image_url")]
    kept.append({"type": "text", "text": "[preview image removed to save context]"})
    try:
        return msg.model_copy(update={"content": kept})
    except Exception:
        return msg


def _make_context_hook(subject: str):
    """create_react_agent pre_model_hook (spec 0004): every model call,
    (1) show the LLM at most MAX_IMAGE_PARTS newest images (older ones -> text
        stub), never touching the first message; and
    (2) every REANCHOR_EVERY model calls, if no image was shown in the last two
        messages, append an ephemeral text reminder of the target subject.
    Uses `llm_input_messages` so the persisted thread keeps full history."""
    from langchain_core.messages import AIMessage, HumanMessage

    def hook(state):
        msgs = list(state["messages"])
        img_idx = [i for i, m in enumerate(msgs) if i != 0 and _msg_has_image(m)]
        stale = set(img_idx[:-MAX_IMAGE_PARTS]) if len(img_idx) > MAX_IMAGE_PARTS else set()
        out = [_strip_images(m) if i in stale else m for i, m in enumerate(msgs)]

        ai_n = sum(1 for m in msgs if isinstance(m, AIMessage))
        recent_img = any(_msg_has_image(m) for m in msgs[-2:])
        if subject and ai_n and ai_n % REANCHOR_EVERY == 0 and not recent_img:
            out = out + [HumanMessage(content=(
                f"Reminder — the target is: {subject[:200]}. If you have not "
                "compared your work to the reference recently, call view_reference()."
            ))]
        return {"llm_input_messages": out}

    return hook


# spec 0005 — which tools each phase may use. view_canvas / view_reference /
# get_pixel are always allowed; anything not listed for a phase is dropped.
_PHASE_TOOLS = {
    "silhouette": {"fill_rect", "fill_row", "fill_column", "draw_line", "draw_circle",
                   "draw_ellipse", "draw_triangle", "draw_rotated_rect"},
    "base_colors": {"fill_rect", "fill_row", "fill_column", "draw_line", "draw_circle",
                    "draw_ellipse", "draw_triangle", "draw_rotated_rect",
                    "draw_pixel", "draw_pixels", "noise_fill_rect", "noise_fill_circle",
                    "voronoi_fill"},
    "shading": None,   # all except finish
    "detail": "ALL",
    "cleanup": "ALL",
}
_ALWAYS_TOOLS = {"view_canvas", "view_reference", "get_pixel"}


def _filter_phase_tools(tools: list, phase: str | None) -> list:
    if not phase or phase not in _PHASE_TOOLS:
        return tools
    allow = _PHASE_TOOLS[phase]
    out = []
    for t in tools:
        if t.name in _ALWAYS_TOOLS:
            out.append(t)
        elif t.name == "finish":
            if allow == "ALL":
                out.append(t)
        elif allow == "ALL" or allow is None or t.name in allow:
            out.append(t)
    return out


def make_tools(canvas: Canvas, vision: bool = True, full_toolset: bool = True,
               reference_img=None, tool_images: bool = True, phase: str | None = None):
    """Create agent tools.

    vision=False -> ASCII-only view_canvas.
    vision=True  -> view_canvas returns a compact text summary; the triptych
                    preview image is injected as a real image message by
                    run_agent_stream's pre_model_hook (spec 0003).
    full_toolset=False drops advanced shape/noise tools.
    `reference_img` is only used by that hook, kept here for signature symmetry.
    """

    @tool
    def draw_pixel(x: int, y: int, color: int) -> str:
        """Set a single pixel at (x, y) to a palette color index. Use -1 for transparent."""
        return canvas.set_pixel(x, y, color)

    @tool
    def draw_pixels(pixels: list[dict]) -> str:
        """Set multiple pixels at once. Each dict has keys: x, y, color. Use this for efficiency when setting many pixels."""
        errors = []
        drawn = 0
        for p in pixels:
            try:
                x = int(p.get("x", p.get("X", 0)))
                y = int(p.get("y", p.get("Y", 0)))
                c = int(p.get("color", p.get("c", p.get("colour", -1))))
                r = canvas.set_pixel(x, y, c)
                if r.startswith("Error"):
                    errors.append(r)
                else:
                    drawn += 1
            except (KeyError, TypeError, ValueError) as e:
                errors.append(f"Bad pixel data: {p} ({e})")
        return f"Drew {drawn} pixels. {len(errors)} errors: {errors[:3]}" if errors else f"Drew {drawn} pixels."

    @tool
    def fill_rect(x1: int, y1: int, x2: int, y2: int, color: int) -> str:
        """Fill a rectangle from (x1,y1) to (x2,y2) inclusive with a palette color index."""
        return canvas.fill_rect(x1, y1, x2, y2, color)

    @tool
    def fill_row(y: int, x_start: int, x_end: int, color: int) -> str:
        """Fill a horizontal row at y from x_start to x_end inclusive."""
        return canvas.fill_row(y, x_start, x_end, color)

    @tool
    def fill_column(x: int, y_start: int, y_end: int, color: int) -> str:
        """Fill a vertical column at x from y_start to y_end inclusive."""
        return canvas.fill_column(x, y_start, y_end, color)

    @tool
    def draw_line(x1: int, y1: int, x2: int, y2: int, color: int) -> str:
        """Draw a 1-pixel-wide line from (x1,y1) to (x2,y2)."""
        return canvas.draw_line(x1, y1, x2, y2, color)

    @tool
    def draw_circle(cx: int, cy: int, radius: int, color: int, fill: bool = True) -> str:
        """Draw a circle. cx,cy = center, radius = size. fill=True for solid, fill=False for outline only."""
        count = canvas.draw_circle(cx, cy, radius, color, fill)
        return f"Drew {'filled' if fill else 'outline'} circle at ({cx},{cy}) r={radius}, {count}px"

    @tool
    def view_canvas():
        """View the current canvas: color legend, fill counts and a spatial layout
        summary (plus an ASCII grid for small canvases). Vision models also get an
        upscaled, coordinate-labelled preview image (reference | current | current
        with a coordinate grid; blue tint = pixels you changed). Call this often."""
        from preview import build_preview_text
        text = build_preview_text(canvas)
        if vision and tool_images:
            block = _preview_image_block(canvas, reference_img)
            if block is not None:
                return [{"type": "text", "text": text}, block]
        return text

    @tool
    def view_reference():
        """Look at the reference image again, side by side with your current sprite.
        Use this whenever you are unsure your sprite still matches the target."""
        if reference_img is None:
            return "No reference image for this sprite — the target is described in the instructions."
        if not (vision and tool_images):
            return "Reference not viewable in this mode; it is described in the instructions."
        blocks = [{"type": "text", "text": "Left: the REFERENCE (your target). Right: your CURRENT sprite."}]
        rb = _image_block_from(reference_img)
        cb = _preview_image_block(canvas, None)
        if rb:
            blocks.append(rb)
        if cb:
            blocks.append(cb)
        return blocks if len(blocks) > 1 else "Reference unavailable."

    @tool
    def get_pixel(x: int, y: int) -> str:
        """Get the palette index at position (x, y)."""
        v = canvas.get_pixel(x, y)
        name = canvas.palette[v] if 0 <= v < len(canvas.palette) else "transparent"
        return f"({x},{y}) = {v} ({name})"

    @tool
    def finish() -> str:
        """Call this when the sprite is complete and you're satisfied with the result."""
        return "FINISHED"

    @tool
    def noise_fill_rect(x1: int, y1: int, x2: int, y2: int, colors: list[int], seed: int = 42, scale: float = 1.0) -> str:
        """Fill a rectangle with noise-distributed colors. Randomly picks from the color list per pixel based on noise. Use different seeds for variation. Scale controls granularity (higher = finer)."""
        count = canvas.fill_noise(x1, y1, x2, y2, colors, seed, scale)
        return f"Noise-filled rect ({x1},{y1})-({x2},{y2}) with {len(colors)} colors, {count}px"

    # Core tools — always included
    core = [
        draw_pixel, draw_pixels, fill_rect, fill_row, fill_column, draw_line,
        draw_circle, noise_fill_rect,
        view_canvas, get_pixel, finish,
    ]
    if reference_img is not None:
        core.append(view_reference)

    if not full_toolset:
        return _filter_phase_tools(core, phase)

    # Advanced tools — only for capable models

    @tool
    def draw_ellipse(cx: int, cy: int, rx: int, ry: int, color: int, fill: bool = True) -> str:
        """Draw an ellipse. cx,cy = center, rx/ry = horizontal/vertical radius. fill=True for solid."""
        count = canvas.draw_ellipse(cx, cy, rx, ry, color, fill)
        return f"Drew {'filled' if fill else 'outline'} ellipse at ({cx},{cy}) rx={rx} ry={ry}, {count}px"

    @tool
    def draw_triangle(x1: int, y1: int, x2: int, y2: int, x3: int, y3: int, color: int) -> str:
        """Draw a filled triangle with 3 corner points."""
        count = canvas.draw_triangle(x1, y1, x2, y2, x3, y3, color)
        return f"Drew triangle ({x1},{y1})-({x2},{y2})-({x3},{y3}), {count}px"

    @tool
    def draw_rotated_rect(cx: int, cy: int, width: int, height: int, angle: float, color: int) -> str:
        """Draw a filled rotated rectangle. cx,cy = center position. width,height = full dimensions. angle = rotation in degrees (0=horizontal, 45=diagonal, etc)."""
        count = canvas.draw_rotated_rect(cx, cy, width, height, angle, color)
        return f"Drew rotated rect at ({cx},{cy}) {width}x{height} angle={angle}deg, {count}px"

    @tool
    def noise_fill_circle(cx: int, cy: int, radius: int, colors: list[int], seed: int = 42) -> str:
        """Fill a circular area with noise-distributed colors. Good for organic patches, spots, texture within a round area."""
        count = canvas.fill_noise_circle(cx, cy, radius, colors, seed)
        return f"Noise-filled circle at ({cx},{cy}) r={radius} with {len(colors)} colors, {count}px"

    @tool
    def voronoi_fill(x1: int, y1: int, x2: int, y2: int, colors: list[int], num_cells: int = 8, seed: int = 42) -> str:
        """Fill a rectangle with Voronoi cell pattern. Creates organic stone-like, cobblestone, or cellular textures. Each cell gets a color from the list. num_cells controls how many cells (more = smaller cells)."""
        count = canvas.fill_voronoi(x1, y1, x2, y2, colors, num_cells, seed)
        return f"Voronoi-filled rect ({x1},{y1})-({x2},{y2}) with {num_cells} cells, {count}px"

    return _filter_phase_tools(
        core + [draw_ellipse, draw_triangle, draw_rotated_rect, noise_fill_circle, voronoi_fill],
        phase,
    )


# ── LLM factory ──

OPENAI_MODEL_PREFIXES = ("gpt-", "o1-", "o3-")

# ── spec 0006 — deterministic drawing config ──

AGENT_TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", "0.2"))

# Per-phase temperature (0005). Precision phases run colder; detail breathes.
PHASE_TEMPERATURE = {
    "silhouette": 0.10,
    "base_colors": 0.15,
    "shading": 0.30,
    "detail": 0.35,
    "cleanup": 0.10,
}


class SamplingConfig:
    """One source of truth for LLM sampling params (spec 0006)."""

    __slots__ = ("temperature", "seed", "top_p")

    def __init__(self, temperature: float = AGENT_TEMPERATURE,
                 seed: int | None = None, top_p: float | None = None):
        self.temperature = temperature
        self.seed = seed
        self.top_p = top_p

    def __repr__(self):
        return f"SamplingConfig(temperature={self.temperature}, seed={self.seed}, top_p={self.top_p})"


def _seed_from(gen_id) -> int:
    import zlib
    return zlib.crc32(str(gen_id).encode()) & 0x7FFFFFFF


def resolve_sampling(*, request_temperature=None, request_seed=None,
                     phase: str | None = None, gen_id=None) -> SamplingConfig:
    """Resolution order: request > phase > env default. Seed: request > gen_id."""
    temp = request_temperature
    if temp is None and phase in PHASE_TEMPERATURE:
        temp = PHASE_TEMPERATURE[phase]
    if temp is None:
        temp = AGENT_TEMPERATURE

    seed = request_seed
    if seed is None and gen_id is not None:
        seed = _seed_from(gen_id)
    return SamplingConfig(temperature=float(temp), seed=seed)

# Ollama config
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODELS = set(m.strip() for m in os.getenv("OLLAMA_MODELS", "").split(",") if m.strip())

# OpenAI / OpenAI-compatible config (Llama.cpp, VLLM, LM Studio, etc.)
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_MODELS_ENV = set(m.strip() for m in os.getenv("OPENAI_MODELS", "").split(",") if m.strip())

def _get_llm(model_name: str, sampling: "SamplingConfig | None" = None,
             temperature: float | None = None):
    """Build a chat model. All sampling params come from `sampling`
    (spec 0006); `temperature` is a back-compat shortcut for callers that only
    have a number."""
    if sampling is None:
        sampling = SamplingConfig(temperature=AGENT_TEMPERATURE if temperature is None else temperature)
    t = sampling.temperature
    seed = sampling.seed

    def _seed_kw() -> dict:
        return {"seed": seed} if seed is not None else {}

    # Ollama models (OpenAI-compatible API)
    if model_name in OLLAMA_MODELS:
        from langchain_openai import ChatOpenAI
        num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
        opts = {"num_ctx": num_ctx}
        if seed is not None:
            opts["seed"] = seed
        return ChatOpenAI(
            model=model_name, temperature=t,
            base_url=f"{OLLAMA_URL}/v1", api_key="ollama",
            extra_body={"options": opts},
        )

    # OpenAI / OpenAI-compatible models
    if model_name in OPENAI_MODELS_ENV or model_name.startswith(OPENAI_MODEL_PREFIXES):
        from langchain_openai import ChatOpenAI
        kwargs: dict = {"model": model_name, "temperature": t, **_seed_kw()}
        if sampling.top_p is not None:
            kwargs["top_p"] = sampling.top_p
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
            if not os.getenv("OPENAI_API_KEY"):
                kwargs["api_key"] = "not-needed"
        return ChatOpenAI(**kwargs)

    # Gemini via API key
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model_name, temperature=t,
            google_api_key=gemini_key.strip().strip("'\""),
            **_seed_kw(),
        )

    # Gemini via Vertex AI (service account)
    import json as _json
    from langchain_google_vertexai import ChatVertexAI
    sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    project = None
    if sa_path and os.path.exists(sa_path):
        with open(sa_path) as f:
            project = _json.load(f).get("project_id")
    return ChatVertexAI(
        model_name=model_name, temperature=t,
        project=project, location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
        **_seed_kw(),
    )


# ── Sessions ──
#
# The old in-memory `_sessions` dict is gone. LangGraph state lives in the
# shared checkpointer (Redis in cloud, Memory for self-host). Canvas pixel
# state is owned by the caller — passed in via `existing_pixels` for
# continuations. Any worker can resume any thread by gen_id.

def cleanup_session(gen_id) -> None:
    """No-op for compatibility. State now lives in the shared checkpointer."""
    return None


def thread_exists(gen_id) -> bool:
    """Return True if a LangGraph thread already exists for this gen_id."""
    cp = get_checkpointer()
    config = {"configurable": {"thread_id": _thread_id_for(gen_id)}}
    try:
        return cp.get(config) is not None
    except Exception:
        return False


# ── System prompt ──

AGENT_TYPE_HINTS = {
    "block": "This is a BLOCK TILE. Fill EVERY pixel — no transparency (-1). The tile will be placed in a grid next to copies of itself. Cover the entire canvas with the material.",
    "icon": "This is an ITEM ICON. Draw the object shape and use -1 (transparent) for the background. Keep it compact, chunky, and recognizable. Leave some transparent padding around the edges.",
    "character": "This is a CHARACTER SPRITE. Draw a character on transparent background (-1). Make the silhouette clear and recognizable. Leave transparent padding around the edges.",
    "freeform": "This is a FREEFORM sprite. Use your best judgment for the composition. If the subject is a standalone object or character, use -1 (transparent) for the background. If it's a scene, pattern, or texture, fill the entire canvas.",
}

def build_system_prompt(user_prompt: str, palette: list[str], size: int,
                        style_prompt: str, has_reference: bool, sprite_type: str = "block",
                        model_name: str = "") -> str:
    palette_desc = "\n".join(
        f"  {i} (char {'A' if i >= 10 else str(i) if i < 10 else chr(ord('A') + i - 10)}): {c}"
        if i >= 10 else f"  {i}: {c}"
        for i, c in enumerate(palette)
    )
    vision = _is_vision_model(model_name)
    full_toolset = vision

    # Tool list depends on model capability
    if full_toolset:
        tools_text = """- fill_rect(x1,y1,x2,y2,color) — fill a rectangle
- fill_row(y,x_start,x_end,color) — fill one row
- fill_column(x,y_start,y_end,color) — fill one column
- draw_line(x1,y1,x2,y2,color) — 1px line
- draw_circle(cx,cy,radius,color,fill) — circle (filled or outline)
- draw_ellipse(cx,cy,rx,ry,color,fill) — ellipse
- draw_triangle(x1,y1,x2,y2,x3,y3,color) — filled triangle
- draw_rotated_rect(cx,cy,w,h,angle,color) — rotated rectangle
- draw_pixel(x,y,color) — single pixel
- draw_pixels([{{"x":0,"y":0,"color":1}},...]) — batch pixels
- noise_fill_rect(x1,y1,x2,y2,colors,seed) — random texture fill
- noise_fill_circle(cx,cy,r,colors,seed) — circular noise fill
- voronoi_fill(x1,y1,x2,y2,colors,cells,seed) — cell/stone patterns
- view_canvas() — see the grid (CALL THIS OFTEN)
- get_pixel(x,y) — check one pixel
- finish() — call when done"""
    else:
        tools_text = """- fill_rect(x1,y1,x2,y2,color) — fill a rectangle
- fill_row(y,x_start,x_end,color) — fill one horizontal row
- fill_column(x,y_start,y_end,color) — fill one vertical column
- draw_line(x1,y1,x2,y2,color) — 1px line between two points
- draw_circle(cx,cy,radius,color,fill) — circle (filled or outline)
- draw_pixel(x,y,color) — set a single pixel
- draw_pixels([{{"x":0,"y":0,"color":1}},...]) — set many pixels at once
- noise_fill_rect(x1,y1,x2,y2,colors,seed) — fill area with random mix of colors (for texture)
- view_canvas() — see the grid (CALL THIS OFTEN to check your work)
- get_pixel(x,y) — check one pixel value
- finish() — call when done"""

    grid_explanation = f"""When you call view_canvas, you see a grid like this:
   0123456789ABCDEF    ← column numbers (hex for 10-15)
 0 ................    ← row 0 (all transparent)
 1 ..0000000000....    ← row 1 (color 0 in columns 2-11)
Each character is a palette index: 0-9 = colors 0-9, A-Z = colors 10-35, . = transparent
Read it like a picture: rows go top to bottom (y), columns go left to right (x).""" if size <= 16 else f"""When you call view_canvas, you see a grid. Each character = one pixel.
0-9 = palette colors 0-9, A-Z = colors 10-35, . = transparent.
Rows = y (top to bottom), columns = x (left to right)."""

    return f"""{style_prompt}

You are a pixel artist. You draw on a {size}x{size} canvas using color indices from a palette.

SUBJECT: {user_prompt}

PALETTE:
{palette_desc}
Use -1 for transparent.

{AGENT_TYPE_HINTS.get(sprite_type, AGENT_TYPE_HINTS["block"])}

{"A reference image is attached. Match its shapes and colors in pixel art." if has_reference else ""}

COORDINATE SYSTEM:
- (0,0) = top-left corner
- ({size-1},{size-1}) = bottom-right corner
- x goes RIGHT (columns), y goes DOWN (rows)

{grid_explanation}

TOOLS:
{tools_text}

WORKFLOW:
1. Plan what to draw — think about the shape, then the colors
2. Fill large areas first with fill_rect
3. Call view_canvas to see your progress
4. Add details with draw_pixel or draw_pixels
5. Call view_canvas again to check
6. Use noise_fill_rect to add texture variation if needed
7. Final view_canvas to verify everything looks right
8. Call finish when done

IMPORTANT: Call view_canvas after every few drawing steps. It shows you exactly what the canvas looks like so you can correct mistakes early."""


# ── Run agent (initial or continuation) ──

def run_agent_stream(
    gen_id,
    message: str,
    palette: list[str],
    size: int,
    model_name: str,
    style_prompt: str = "",
    sprite_type: str = "block",
    reference_b64: str | None = None,
    on_step: Any = None,
    max_steps: int = 80,
    existing_pixels: list[list[int]] | None = None,
    cancel_check: Any = None,
    seed_mode: str = "off",
    phase: str | None = None,
    temperature: float | None = None,
    seed: int | None = None,
):
    """
    Run the agent or continue an existing session.

    First call (no thread for `gen_id`) creates the session and seeds it with the
    full system prompt. Subsequent calls (thread already exists in checkpointer)
    continue the conversation as a chat edit.

    `cancel_check`, if provided, is a zero-arg callable returning True when the
    job has been canceled. The stream loop checks it after every step and exits
    cleanly (drains remaining chunks to avoid GeneratorExit in callbacks).
    """
    is_new = not thread_exists(gen_id)
    thread_id = _thread_id_for(gen_id)

    # spec 0002 — reference-seeded canvas. On a fresh generation with a reference
    # and seed_mode on, start from a quantized underlay of the reference instead
    # of a blank canvas. `locked` freezes the silhouette (opaque<->transparent).
    silhouette = None
    locked = seed_mode == "locked"
    if is_new and seed_mode in ("soft", "locked") and reference_b64 and not _has_content(existing_pixels):
        try:
            from jobs._seed import build_seed_from_b64
            existing_pixels, silhouette = build_seed_from_b64(
                reference_b64, size, sprite_type, palette
            )
        except Exception:
            silhouette, locked = None, False

    # Build the canvas from the caller-provided pixel state. Canvas pixel state
    # lives outside the LangGraph thread (it's owned by the job system, not
    # the conversation). LangGraph just owns the message history.
    canvas = Canvas(size, palette, existing_pixels, silhouette=silhouette, locked=locked)
    # Snapshot of the underlay so progress consumers can highlight agent edits.
    canvas.seed_pixels = [row[:] for row in canvas.pixels] if silhouette is not None else None

    vision = _is_vision_model(model_name)
    full_toolset = vision  # small local models get the simplified toolset

    # Decode the reference once for the preview triptych (spec 0003).
    reference_img = None
    if vision and reference_b64:
        try:
            import base64 as _b64, io as _io
            reference_img = Image.open(_io.BytesIO(_b64.b64decode(reference_b64))).convert("RGBA")
        except Exception:
            reference_img = None

    # spec 0003 — view_canvas returns an upscaled, coordinate-labelled triptych
    # image directly in its tool result (Gemini / Vertex / local vision models
    # accept image content in tool messages). OpenAI's function role is text-only,
    # so those get the improved text summary.
    tool_images = vision and _supports_tool_images(model_name)
    tools = make_tools(canvas, vision=vision, full_toolset=full_toolset,
                       reference_img=reference_img, tool_images=tool_images, phase=phase)
    sampling = resolve_sampling(request_temperature=temperature, request_seed=seed,
                                phase=phase, gen_id=gen_id)
    llm = _get_llm(model_name, sampling)
    checkpointer = get_checkpointer()
    # spec 0004 — keep the target in view (periodic re-anchor) and cap how many
    # preview images the LLM carries at once.
    agent = create_react_agent(
        llm, tools, checkpointer=checkpointer,
        pre_model_hook=_make_context_hook(message),
    )

    # A fresh thread that was handed a pre-filled canvas (e.g. the image-first
    # pipeline's refine pass) is editing, not creating from scratch.
    seeded = is_new and _has_content(existing_pixels)

    if is_new:
        sys_prompt = build_system_prompt(message, palette, size, style_prompt, reference_b64 is not None, sprite_type, model_name)
        if seeded:
            lock_note = (
                "\nThe silhouette is LOCKED: drawing that would add or remove the "
                "subject's outline is ignored — you can only change colors and "
                "detail inside the existing shape."
                if locked else
                "\nThe underlay is a suggestion — you may reshape it where the "
                "reference clearly disagrees."
            )
            sys_prompt += f"""

IMPORTANT — THE CANVAS IS NOT BLANK.
It already contains a faithful first-pass conversion of the reference. Your job
is to REFINE it, not rebuild it: fix edges, fix wrong colors, add readable
detail, then finish. Call view_canvas first.
Do NOT clear the canvas or fill large areas with -1 to "start over" — work from
what is already there.{lock_note}

CURRENT CANVAS STATE:
{canvas.to_grid_string()}"""
        user_parts = [{"type": "text", "text": sys_prompt}]
        if reference_b64:
            user_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{reference_b64}"},
            })
        input_message = HumanMessage(content=user_parts)
    else:
        # Follow-up: include current canvas state so the agent knows what it's editing
        grid = canvas.to_grid_string()
        follow_up = f"""The user wants you to make changes to the current sprite.

CURRENT CANVAS STATE:
{grid}

USER REQUEST: {message}

Use the canvas tools to make the requested changes. Call finish when done."""
        parts: list = [{"type": "text", "text": follow_up}]
        if reference_b64:  # spec 0004 — continuations get the reference too
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:image/png;base64,{reference_b64}"}})
        input_message = HumanMessage(content=parts)

    config = {"configurable": {"thread_id": thread_id}}

    # Add PostHog callback if configured
    ph_callback = _get_posthog_callback(distinct_id=str(gen_id), trace_id=f"gen_{gen_id}")
    if ph_callback:
        config["callbacks"] = [ph_callback]

    step_count = 0
    finished = False

    # Consume the full stream — don't break early to avoid GeneratorExit in LangSmith
    for chunk in agent.stream(
        {"messages": [input_message]},
        config=config,
        stream_mode="updates",
    ):
        if finished:
            continue  # drain remaining chunks without processing

        # Cooperative cancel check — if the job was canceled, stop driving the
        # loop but keep draining so callbacks unwind cleanly.
        if cancel_check is not None:
            try:
                if cancel_check():
                    finished = True
                    if on_step:
                        on_step(canvas, "canceled", "Job canceled by user")
                    continue
            except Exception:
                pass

        for node_name, node_data in chunk.items():
            messages = node_data.get("messages", [])
            for msg in messages:
                step_count += 1

                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    # Agent decided to call a tool — log it but DON'T snapshot pixels yet
                    # (the tool hasn't executed, canvas hasn't changed)
                    for tc in msg.tool_calls:
                        info = f"Tool: {tc['name']}({json.dumps(tc['args'], separators=(',', ':'))})"
                        if on_step:
                            on_step(canvas, "tool_call", info)

                elif hasattr(msg, "content") and isinstance(msg.content, str):
                    content = msg.content.strip()
                    if "FINISHED" in (msg.content or ""):
                        finished = True
                    # Tool results come from the "tools" node — canvas has been updated
                    if node_name == "tools" and on_step:
                        on_step(canvas, "tool_result", content[:200])
                    elif content and on_step:
                        on_step(canvas, "thought", content[:200])

                if step_count >= max_steps:
                    finished = True

    return canvas


# ── spec 0005 — silhouette-first phased workflow ──

# (name, budget weight of the global step cap, instruction). Weights sum to 1.0.
AGENT_PHASES = [
    ("silhouette", 0.22, """PHASE 1 — SILHOUETTE.
Block out ONLY the overall shape: which pixels are the subject (opaque) and which
stay background (-1). Use fill_rect / fill_row / fill_column / draw_circle /
draw_ellipse — coarse shapes only, NO single pixels. Match the reference outline.
When the silhouette reads clearly, STOP: reply with one short sentence and make no
more tool calls."""),
    ("base_colors", 0.22, """PHASE 2 — BASE COLORS.
Flat-fill each region of the shape with its correct palette colour from the
reference. No shading, no outline yet. When every area has its base colour, STOP."""),
    ("shading", 0.20, """PHASE 3 — SHADING.
Add 1-2 tone steps: lighter where light hits, darker on the underside / in shadow,
following the reference. Keep it readable — do not over-dither. When shading reads,
STOP."""),
    ("detail", 0.24, """PHASE 4 — DETAIL.
Tidy the edges and add the small features that make the subject recognizable at 1x
(eyes, highlights, texture accents). Call finish() when it looks done."""),
    ("cleanup", 0.12, """PHASE 5 — CLEANUP.
Remove stray pixels and fix broken edges. Block tile: check it will tile
seamlessly. Icon / character: check the transparent padding. Call finish()."""),
]

SILHOUETTE_IOU_SEEDED = float(os.getenv("SILHOUETTE_IOU_SEEDED", "0.85"))
SILHOUETTE_IOU_BLANK = float(os.getenv("SILHOUETTE_IOU_BLANK", "0.70"))
SILHOUETTE_RETRIES = int(os.getenv("SILHOUETTE_RETRIES", "2"))


def run_phased_generation(
    gen_id,
    message: str,
    palette: list[str],
    size: int,
    model_name: str,
    style_prompt: str = "",
    sprite_type: str = "block",
    reference_b64: str | None = None,
    on_step: Any = None,
    max_steps: int = 80,
    existing_pixels: list[list[int]] | None = None,
    cancel_check: Any = None,
    seed_mode: str = "off",
    temperature: float | None = None,
    seed: int | None = None,
):
    """Drive generation through the fixed phases (spec 0005), one agent call per
    phase on the same LangGraph thread. After the silhouette phase, gate on IoU
    vs the reference-derived mask and re-run that phase up to SILHOUETTE_RETRIES
    times before moving on."""
    from jobs._render_core import iou, silhouette_of

    ref_mask = None
    if reference_b64:
        try:
            from jobs._seed import build_seed_from_b64
            _, ref_mask = build_seed_from_b64(reference_b64, size, sprite_type, palette)
        except Exception:
            ref_mask = None

    seeded = _has_content(existing_pixels) or (seed_mode in ("soft", "locked") and reference_b64)
    gate = SILHOUETTE_IOU_SEEDED if seeded else SILHOUETTE_IOU_BLANK

    pixels = existing_pixels
    canvas = Canvas(size, palette, pixels)

    def _emit_phase(name: str):
        if on_step:
            on_step(canvas, "phase", name)

    for pname, weight, instr in AGENT_PHASES:
        if cancel_check and cancel_check():
            break
        budget = max(6, round(max_steps * weight))
        _emit_phase(pname)

        canvas = run_agent_stream(
            gen_id=gen_id, message=f"{message}\n\n{instr}", palette=palette, size=size,
            model_name=model_name, style_prompt=style_prompt, sprite_type=sprite_type,
            reference_b64=reference_b64, on_step=on_step, max_steps=budget,
            existing_pixels=pixels, cancel_check=cancel_check,
            seed_mode=(seed_mode if pixels is None else "off"), phase=pname,
            temperature=temperature, seed=seed,
        )
        pixels = [row[:] for row in canvas.pixels]

        if pname == "silhouette" and ref_mask:
            for attempt in range(1, SILHOUETTE_RETRIES + 1):
                score = iou(silhouette_of(pixels), ref_mask)
                if score >= gate:
                    break
                _emit_phase(f"silhouette-retry-{attempt} (IoU {score:.2f})")
                canvas = run_agent_stream(
                    gen_id=gen_id, size=size, palette=palette, model_name=model_name,
                    style_prompt=style_prompt, sprite_type=sprite_type,
                    reference_b64=reference_b64, on_step=on_step, max_steps=budget,
                    existing_pixels=pixels, cancel_check=cancel_check, seed_mode="off",
                    phase="silhouette", temperature=temperature, seed=seed,
                    message=(f"{message}\n\nThe silhouette only overlaps the reference "
                             f"{score:.0%}. Fix the OUTLINE: add the missing parts, remove "
                             "the extra parts, coarse shapes only. STOP when it matches."),
                )
                pixels = [row[:] for row in canvas.pixels]

    return canvas
