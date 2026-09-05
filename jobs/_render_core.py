"""Pure image → indexed-pixel-grid pipeline for the image-first generation path.

No network or storage I/O lives here: every function takes a PIL image (or a
grid) and returns a PIL image or a grid. That keeps the whole pipeline
unit-testable without a running server, and lets both the `sprite.render` and
`sprite.from_photo` job handlers share one implementation.

Pipeline (see specs/0001-image-first-pipeline.md):

    derive_palette   reference image        -> list[hex]
    prepare          reference, size, type  -> RGBA image at size x size
    quantize         prepared, palette      -> int[][]  (-1 = transparent)
    remove_background grid, palette, type   -> int[][]  (edge flood fill -> -1)
    despeckle        grid                   -> int[][]  (kill lone pixels)
    render_png       grid, palette, size    -> RGBA image

`render()` ties them together.
"""

from __future__ import annotations

from collections import Counter, deque

from PIL import Image, ImageEnhance

Grid = list[list[int]]

# Sprite types whose background should be knocked out to transparency.
_BG_REMOVAL_TYPES = {"icon", "character", "freeform"}


# ── color helpers ──

def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(rgb: tuple[int, int, int] | tuple[int, ...]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def nearest_index(rgb: tuple[int, ...], palette_rgb: list[tuple[int, int, int]]) -> int:
    """Index of the closest palette entry to `rgb` by squared Euclidean distance."""
    r, g, b = rgb[0], rgb[1], rgb[2]
    best = 0
    best_d2 = 1 << 30
    for i, (pr, pg, pb) in enumerate(palette_rgb):
        d2 = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = i
            if d2 == 0:
                break
    return best


# ── palette derivation ──

def derive_palette(img: Image.Image, max_colors: int = 16) -> list[str]:
    """Dominant colors of the opaque pixels of `img`, sorted dark → light.

    Mirrors server.extract_reference_palette so there is a single implementation.
    """
    rgba = img.convert("RGBA")
    opaque = [p[:3] for p in rgba.getdata() if p[3] >= 128]
    if not opaque:
        return ["#000000"]

    counts = Counter(opaque)
    if len(counts) <= max_colors:
        raw = [c for c, _ in counts.most_common(max_colors)]
    else:
        flat = Image.new("RGB", (len(opaque), 1))
        flat.putdata(opaque)
        q = flat.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[: max_colors * 3]
        raw = [(pal[i], pal[i + 1], pal[i + 2]) for i in range(0, len(pal), 3)]

    raw.sort(key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])
    return [rgb_to_hex(c) for c in raw]


# ── prepare ──

def prepare(img: Image.Image, size: int, sprite_type: str) -> Image.Image:
    """Return an RGBA image at `size`×`size`, lightly sharpened before scaling.

    For block tiles any source transparency is flattened onto the image's own
    average color so the whole canvas ends up filled.
    """
    rgba = img.convert("RGBA")

    if sprite_type == "block" and _has_transparency(rgba):
        bg = _average_opaque_color(rgba)
        flat = Image.new("RGBA", rgba.size, bg + (255,))
        flat.alpha_composite(rgba)
        rgba = flat

    # Sharpen a touch so edges survive the downscale; skip when upscaling.
    if rgba.width > size or rgba.height > size:
        rgba = ImageEnhance.Sharpness(rgba).enhance(1.6)
        resample = Image.Resampling.LANCZOS
    else:
        resample = Image.Resampling.NEAREST

    return rgba.resize((size, size), resample)


def _has_transparency(rgba: Image.Image) -> bool:
    return any(p[3] < 250 for p in rgba.getdata())


def _average_opaque_color(rgba: Image.Image) -> tuple[int, int, int]:
    opaque = [p[:3] for p in rgba.getdata() if p[3] >= 128]
    if not opaque:
        return (0, 0, 0)
    n = len(opaque)
    return tuple(sum(c[i] for c in opaque) // n for i in range(3))  # type: ignore[return-value]


# ── quantize ──

def quantize(
    img: Image.Image,
    palette: list[str],
    *,
    keep_alpha: bool = True,
    alpha_threshold: int = 128,
) -> Grid:
    """Snap every pixel of `img` to the nearest palette index.

    Pixels with alpha below `alpha_threshold` become -1 when `keep_alpha`.
    """
    rgba = img.convert("RGBA")
    w, h = rgba.size
    pal_rgb = [hex_to_rgb(c) for c in palette]
    px = rgba.load()
    grid: Grid = []
    for y in range(h):
        row: list[int] = []
        for x in range(w):
            r, g, b, a = px[x, y]
            if keep_alpha and a < alpha_threshold:
                row.append(-1)
            else:
                row.append(nearest_index((r, g, b), pal_rgb))
        grid.append(row)
    return grid


# ── background removal ──

def remove_background(
    grid: Grid,
    palette: list[str],
    *,
    tolerance: float = 40.0,
) -> Grid:
    """Flood fill inward from the four edges, turning background-like pixels -1.

    A pixel is background if it is reachable from an edge through pixels whose
    color stays within `tolerance` (Euclidean RGB) of the seed edge color.
    Interior pixels that merely share the background color are left alone.
    """
    if not grid or not grid[0]:
        return grid
    h, w = len(grid), len(grid[0])
    pal_rgb = [hex_to_rgb(c) for c in palette]

    def color_of(idx: int) -> tuple[int, int, int] | None:
        if 0 <= idx < len(pal_rgb):
            return pal_rgb[idx]
        return None

    out = [row[:] for row in grid]
    visited = [[False] * w for _ in range(h)]
    q: deque[tuple[int, int, tuple[int, int, int]]] = deque()

    # Only flood from border pixels that match the *dominant* border color, so a
    # subject that merely clips the frame (a leg, a staff) is not mistaken for
    # background.
    border_idx: list[int] = (
        [grid[0][x] for x in range(w)]
        + [grid[h - 1][x] for x in range(w)]
        + [grid[y][0] for y in range(h)]
        + [grid[y][w - 1] for y in range(h)]
    )
    opaque_border = [i for i in border_idx if color_of(i) is not None]
    if not opaque_border:
        return out
    dominant = Counter(opaque_border).most_common(1)[0][0]
    dom_rgb = color_of(dominant)
    tol2 = tolerance * tolerance

    def _seed_if_bg(x: int, y: int) -> None:
        if visited[y][x]:
            return
        c = color_of(grid[y][x])
        visited[y][x] = True
        if c is None:
            return
        if sum((c[i] - dom_rgb[i]) ** 2 for i in range(3)) <= tol2:
            q.append((x, y, dom_rgb))

    for x in range(w):
        _seed_if_bg(x, 0)
        _seed_if_bg(x, h - 1)
    for y in range(h):
        _seed_if_bg(0, y)
        _seed_if_bg(w - 1, y)

    while q:
        x, y, seed = q.popleft()
        out[y][x] = -1
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= nx < w and 0 <= ny < h) or visited[ny][nx]:
                continue
            c = color_of(grid[ny][nx])
            if c is None:  # already transparent
                visited[ny][nx] = True
                continue
            d2 = sum((c[i] - seed[i]) ** 2 for i in range(3))
            if d2 <= tol2:
                visited[ny][nx] = True
                q.append((nx, ny, seed))
    return out


# ── despeckle ──

def despeckle(grid: Grid) -> Grid:
    """Reassign any pixel whose four orthogonal neighbors are all one other value."""
    if not grid or not grid[0]:
        return grid
    h, w = len(grid), len(grid[0])
    out = [row[:] for row in grid]
    for y in range(h):
        for x in range(w):
            neighbors = []
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    neighbors.append(grid[ny][nx])
            if len(neighbors) >= 3 and len(set(neighbors)) == 1 and neighbors[0] != grid[y][x]:
                out[y][x] = neighbors[0]
    return out


# ── render ──

def render_png(grid: Grid, palette: list[str], size: int) -> Image.Image:
    pal_rgb = [hex_to_rgb(c) for c in palette]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(grid):
        for x, idx in enumerate(row):
            if 0 <= idx < len(pal_rgb):
                r, g, b = pal_rgb[idx]
                px[x, y] = (r, g, b, 255)
    return img


def render(
    img: Image.Image,
    *,
    size: int,
    sprite_type: str = "block",
    colors: list[str] | None = None,
    max_colors: int = 16,
    cleanup: bool = True,
    remove_bg: bool | None = None,
    bg_tolerance: float = 40.0,
) -> tuple[Grid, list[str]]:
    """Full image-first pipeline. Returns (grid, palette)."""
    palette = colors if colors else derive_palette(img, max_colors)

    prepared = prepare(img, size, sprite_type)
    grid = quantize(prepared, palette, keep_alpha=(sprite_type != "block"))

    if remove_bg is None:
        remove_bg = sprite_type in _BG_REMOVAL_TYPES
    if remove_bg and sprite_type != "block":
        grid = remove_background(grid, palette, tolerance=bg_tolerance)

    if cleanup:
        grid = despeckle(grid)

    return grid, palette


# ── silhouette compare (spec 0005) ──

def silhouette_of(grid: Grid) -> set:
    """Set of (x, y) that are opaque (palette index >= 0)."""
    return {(x, y) for y, row in enumerate(grid) for x, v in enumerate(row) if v >= 0}


def iou(a: set, b: set) -> float:
    """Intersection-over-union of two pixel sets. 1.0 when both empty."""
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0
