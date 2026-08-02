"""Figure detection and description.

Goal: the reader should never need to open the source PDF to find out *what is
on a page*. Every graphic gets an inline block naming its type, size, caption and
every text label found inside it. Where the geometry is genuinely recoverable
(boxes joined by lines) we emit Mermaid; otherwise we describe and do not invent.

Deliberately NOT attempted: rendering drawings or charts as ASCII art. A plan
sheet holds thousands of coordinates; an 80x40 character grid cannot carry them,
and a wrong-but-plausible diagram is far more dangerous in evidence than a
stated gap.

The pure-geometry helpers take plain tuples so they can be unit-tested without
PyMuPDF or a real document.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

BBox = tuple[float, float, float, float]

CAPTION_RE = re.compile(
    r"^\s*(fig(?:ure)?|drawing|dwg|chart|graph|plate|sketch|exhibit|annexure|"
    r"appendix|schedule|photo|image|diagram)\b[\s.:\-—]*\S",
    re.IGNORECASE)

# A label is a short piece of text sitting inside the artwork, not a paragraph.
MAX_LABEL_CHARS = 48
MAX_LABELS = 40
# A single plan sheet can carry hundreds of thousands of vector paths. Beyond
# this we still describe the figure but stop walking items for flowchart
# reconstruction — it could not be a flowchart at that density anyway.
MAX_PATHS_FOR_STRUCTURE = 4000


@dataclass
class Figure:
    kind: str                       # "vector drawing" | "image" | "mixed"
    coverage: float                 # fraction of the page area, 0-1
    bbox: BBox
    objects: int = 0                # vector path count
    images: int = 0
    caption: str = ""
    labels: list[str] = field(default_factory=list)
    mermaid: str = ""
    image_file: str = ""

    def summary(self) -> str:
        bits = [self.kind]
        if self.objects:
            bits.append(f"{self.objects} objects")
        if self.images > 1:
            bits.append(f"{self.images} images")
        bits.append(f"{self.coverage:.0%} of page")
        return ", ".join(bits)


# --------------------------------------------------------------------------
# pure geometry helpers (unit-testable)
# --------------------------------------------------------------------------
def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _inside(inner: BBox, outer: BBox, pad: float = 2.0) -> bool:
    return (inner[0] >= outer[0] - pad and inner[1] >= outer[1] - pad
            and inner[2] <= outer[2] + pad and inner[3] <= outer[3] + pad)


def _overlaps(a: BBox, b: BBox) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _near_bbox(pt: tuple[float, float], b: BBox, tol: float) -> bool:
    """Is the point on or just outside the border of this box?"""
    x, y = pt
    if not (b[0] - tol <= x <= b[2] + tol and b[1] - tol <= y <= b[3] + tol):
        return False
    dx = min(abs(x - b[0]), abs(x - b[2]))
    dy = min(abs(y - b[1]), abs(y - b[3]))
    inside = b[0] <= x <= b[2] and b[1] <= y <= b[3]
    return (dx <= tol or dy <= tol) or not inside


def cluster_ids(boxes: list[BBox], tol: float) -> list[int]:
    """Union-find grouping of boxes that touch or sit within `tol` of each other.

    Returns one cluster id per input box (same order). Used to check whether
    everything build_mermaid is about to be fed forms one spatially-connected
    diagram, or several unrelated ones that merely happen to share a page —
    e.g. a signature block and an unrelated flowchart. Without this, geometry
    from two unrelated drawings could get treated as one, and a line from
    drawing A could "connect" a box in drawing B purely by coincidence of
    position — inventing a relationship between them that the page never drew.
    """
    n = len(boxes)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def close(b1: BBox, b2: BBox) -> bool:
        return not (b1[2] + tol < b2[0] or b2[2] + tol < b1[0]
                    or b1[3] + tol < b2[1] or b2[3] + tol < b1[1])

    for i in range(n):
        for j in range(i + 1, n):
            if close(boxes[i], boxes[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    return [find(i) for i in range(n)]


def build_mermaid(nodes: list[tuple[BBox, str]],
                  segments: list[tuple[tuple[float, float], tuple[float, float]]],
                  tol: float = 14.0) -> str | None:
    """Reconstruct a box-and-arrow diagram as Mermaid, or return None.

    Conservative on purpose: if the shape isn't clearly a flowchart we say
    nothing rather than emit a misleading graph.
    """
    labelled = [(b, t.strip()) for b, t in nodes if t and t.strip()]
    if not (3 <= len(labelled) <= 25):
        return None

    edges: set[tuple[int, int]] = set()
    for p1, p2 in segments:
        a = b = None
        for idx, (box, _) in enumerate(labelled):
            if a is None and _near_bbox(p1, box, tol):
                a = idx
            if b is None and _near_bbox(p2, box, tol):
                b = idx
        if a is not None and b is not None and a != b:
            edges.add((a, b) if a < b else (b, a))

    if len(edges) < 2 or len(edges) < len(labelled) - 1:
        return None                      # too sparse to be a real flow
    # every node should participate, otherwise we are guessing
    touched = {i for e in edges for i in e}
    if len(touched) < len(labelled):
        return None

    # order top-to-bottom, then left-to-right, so the Mermaid reads naturally
    order = sorted(range(len(labelled)), key=lambda i: (labelled[i][0][1], labelled[i][0][0]))
    rank = {old: new for new, old in enumerate(order)}

    def clean(t: str) -> str:
        t = " ".join(t.split())
        t = t.replace('"', "'")
        return t[:60]

    lines = ["```mermaid", "flowchart TD"]
    for old in order:
        lines.append(f'    N{rank[old]}["{clean(labelled[old][1])}"]')
    for a, b in sorted(edges, key=lambda e: (rank[e[0]], rank[e[1]])):
        lo, hi = sorted((rank[a], rank[b]))
        lines.append(f"    N{lo} --> N{hi}")
    lines.append("```")
    return "\n".join(lines)


def pick_caption(candidates: list[tuple[BBox, str]], fig: BBox,
                 max_gap: float = 60.0) -> str:
    """Nearest caption-looking line directly above or below the artwork."""
    best, best_gap = "", max_gap
    for box, text in candidates:
        line = " ".join(text.split())
        if not CAPTION_RE.match(line):
            continue
        if box[2] < fig[0] or box[0] > fig[2]:      # not horizontally aligned
            continue
        gap = box[1] - fig[3] if box[1] >= fig[3] else fig[1] - box[3]
        if 0 <= gap < best_gap:
            best, best_gap = line[:200], gap
    return best


# --------------------------------------------------------------------------
# PyMuPDF-facing extraction
# --------------------------------------------------------------------------
def analyse(page, min_coverage: float = 0.06,
            want_mermaid: bool = True) -> Figure | None:
    """Return a Figure when the page carries meaningful artwork, else None.

    `page` is an app.pdfengine.Page.
    """
    try:
        page_box: BBox = page.rect
    except Exception:
        return None
    page_area = _area(page_box) or 1.0

    geo = page.geometry()
    img_rects = list(geo.images)
    shapes: list[BBox] = list(geo.rects)
    for (x0, y0), (x1, y1) in geo.lines:
        shapes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    if geo.path_count < 8 and not img_rects:
        return None                       # rules, underlines, table borders

    box: BBox | None = None
    for rb in shapes + img_rects:
        box = rb if box is None else _union(box, rb)
    if box is None:
        return None

    coverage = _area(box) / page_area
    if coverage < min_coverage:
        return None

    if shapes and img_rects:
        kind = "mixed drawing and image"
    elif img_rects:
        kind = "image" if len(img_rects) == 1 else "images"
    else:
        kind = "vector drawing"

    fig = Figure(kind=kind, coverage=min(coverage, 1.0), bbox=box,
                 objects=geo.path_count, images=len(img_rects))

    # -- text: labels inside the artwork, captions just outside it
    spans: list[tuple[BBox, str]] = []
    try:
        for sp in page.spans():
            if sp.text.strip():
                spans.append((sp.bbox, sp.text))
    except Exception:
        pass

    labels: list[str] = []
    for sbox, txt in spans:
        clean = " ".join(txt.split())
        if not clean or len(clean) > MAX_LABEL_CHARS:
            continue
        if _inside(sbox, box) or (_overlaps(sbox, box) and len(clean) <= 24):
            if clean not in labels:
                labels.append(clean)
    fig.labels = labels[:MAX_LABELS]
    fig.caption = pick_caption(spans, box)

    # -- flowchart reconstruction, only where the geometry supports it
    if want_mermaid and geo.path_count <= MAX_PATHS_FOR_STRUCTURE:
        nodes: list[tuple[BBox, str]] = []
        for rb in geo.rects:
            if _area(rb) > 400:
                inner = [" ".join(t.split()) for sb, t in spans if _inside(sb, rb)]
                nodes.append((rb, " ".join(inner)))
        segs = list(geo.lines)
        if nodes and segs:
            node_boxes = [nb for nb, _ in nodes]
            seg_boxes = [(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                        for (x0, y0), (x1, y1) in segs]
            combined = node_boxes + seg_boxes
            # Bounded: this is O(n^2); real flowcharts are a handful of boxes,
            # and MAX_PATHS_FOR_STRUCTURE already keeps geo.path_count sane, but
            # guard the pathological case explicitly rather than trust that.
            single_diagram = len(combined) > 600 or len(
                {cid for cid in cluster_ids(combined, tol=14.0)[:len(node_boxes)]}) <= 1
            if single_diagram:
                try:
                    fig.mermaid = build_mermaid(nodes, segs) or ""
                except Exception:
                    fig.mermaid = ""
    return fig


def export_image(page, outdir: Path, page_no: int, dpi: int = 150) -> str:
    outdir.mkdir(parents=True, exist_ok=True)
    name = f"page_{page_no:04d}.png"
    page.save_png(outdir / name, dpi=dpi)
    return name


def render(fig: Figure, page_no: int) -> str:
    """The markdown block describing one figure."""
    out = [f"### Figure — page {page_no}", "", f"Type: {fig.summary()}"]
    if fig.caption:
        out.append(f"Caption: {fig.caption}")
    if fig.labels:
        out.append("Labels found in the artwork: " +
                   "; ".join(f"`{l}`" for l in fig.labels))
    if fig.image_file:
        out.append(f"Page image: `{fig.image_file}` (upload alongside this file "
                   f"to have the drawing itself examined)")
    if fig.mermaid:
        out += ["", "Reconstructed structure (derived from the page geometry — "
                    "verify against the original before relying on it):", "", fig.mermaid]
    elif not fig.labels and not fig.caption:
        out.append("No text could be recovered from this graphic.")
    return "\n".join(out)
