#!/usr/bin/env python3
"""
text_collision_check.py — flag overlapping `<text>` bboxes in an SVG.

The LLM-writes-SVG path (SKILL.md §1.0a) has one well-known failure mode:
two `<text>` nodes end up at coordinates that visually overlap. This is
the script SKILL.md §1.0a's `# python scripts/text_collision_check.py` hint
points at.

Usage:
  python text_collision_check.py figure.svg
  python text_collision_check.py figure.svg --threshold 4
  python text_collision_check.py figure.svg --render-overlay collisions.svg

Exits 0 if no overlaps; non-zero with structured stderr output otherwise.

Bbox derivation strategy:
  - Prefer Inkscape `--query-{x,y,width,height}` per element when an
    `inkscape` binary is on PATH. Inkscape resolves text shaping +
    inheritance + transforms exactly.
  - Fall back to a heuristic that parses `x` / `y` / `font-size` /
    `text-anchor` directly. Width estimated as
        text_length * font_size * 0.55  (roughly Arial average advance)
    Height ≈ font_size * 1.2.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lxml import etree


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
SVG_TAG = "{http://www.w3.org/2000/svg}"


@dataclass(frozen=True)
class Bbox:
    """A rectangle, all values in viewBox user units."""
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def overlap_area(self, other: "Bbox") -> float:
        ox1 = max(self.x, other.x)
        oy1 = max(self.y, other.y)
        ox2 = min(self.x2, other.x2)
        oy2 = min(self.y2, other.y2)
        if ox2 <= ox1 or oy2 <= oy1:
            return 0.0
        return (ox2 - ox1) * (oy2 - oy1)


def _heuristic_bbox(elem: etree._Element) -> Bbox | None:
    """Estimate a `<text>` bbox from x / y / font-size / text-anchor / text length."""
    try:
        x = float(elem.get("x") or 0.0)
        y = float(elem.get("y") or 0.0)
    except ValueError:
        return None

    fs_raw = elem.get("font-size") or _walk_inherited(elem, "font-size") or "16"
    try:
        fs = float(fs_raw.replace("px", "").replace("pt", ""))
    except ValueError:
        fs = 16.0
    # Concatenate the entire text subtree (text + every <tspan>'s text +
    # tail text). The previous one-level-deep walk missed common
    # `<text><tspan>A</tspan> tail</text>` patterns that produce a real
    # bbox on screen but read as empty here, hiding overlaps.
    text = "".join(elem.itertext()).strip()

    if not text:
        return None

    # Crude advance-width heuristic. Real proportional fonts vary, but this
    # is good enough to spot gross overlaps.
    width = max(len(text) * fs * 0.55, fs)
    height = fs * 1.2

    # `text-anchor` inherits down the SVG tree, so a single `<g
    # text-anchor="middle">` wrapper around several `<text>` children
    # was previously unseen and made the heuristic mis-place each cell.
    anchor = (_walk_inherited(elem, "text-anchor") or "start").lower()
    if anchor == "middle":
        x -= width / 2
    elif anchor == "end":
        x -= width

    # SVG `<text>` y is the baseline; bbox top is roughly y - 0.8 * fs.
    top = y - 0.8 * fs
    return Bbox(x=x, y=top, w=width, h=height)


def _walk_inherited(elem: etree._Element, attr: str) -> str | None:
    cur: etree._Element | None = elem
    while cur is not None:
        v = cur.get(attr)
        if v:
            return v
        cur = cur.getparent()
    return None


def _inkscape_bbox(svg_path: Path, element_id: str) -> Bbox | None:
    """Ask Inkscape to compute the bbox; returns None on any error."""
    try:
        out = subprocess.run(
            [
                "inkscape", str(svg_path),
                f"--query-id={element_id}",
                "--query-x", "--query-y",
                "--query-width", "--query-height",
            ],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    parts = out.strip().splitlines()
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = map(float, parts)
    except ValueError:
        return None
    return Bbox(x=x, y=y, w=w, h=h)


def collect_text_bboxes(svg_path: Path, *, use_inkscape: bool) -> list[tuple[etree._Element, Bbox]]:
    """Return [(text_element, bbox), …] for every <text> in the document.

    `use_inkscape=True` uses Inkscape's exact-shaping bbox per element; falls
    back to the heuristic when Inkscape is unavailable or errors on a node.
    """
    tree = etree.parse(str(svg_path))
    root = tree.getroot()
    text_elements = root.findall(".//svg:text", SVG_NS)
    inkscape_available = use_inkscape and shutil.which("inkscape") is not None

    pairs: list[tuple[etree._Element, Bbox]] = []
    for elem in text_elements:
        bbox: Bbox | None = None
        eid = elem.get("id")
        if inkscape_available and eid:
            bbox = _inkscape_bbox(svg_path, eid)
        if bbox is None:
            bbox = _heuristic_bbox(elem)
        if bbox is not None:
            pairs.append((elem, bbox))
    return pairs


def find_overlaps(
    pairs: list[tuple[etree._Element, Bbox]],
    *,
    threshold: float,
) -> list[tuple[etree._Element, etree._Element, float]]:
    """Return [(text_a, text_b, overlap_area)] for every pair > threshold."""
    overlaps: list[tuple[etree._Element, etree._Element, float]] = []
    for i in range(len(pairs)):
        a_elem, a_box = pairs[i]
        for j in range(i + 1, len(pairs)):
            b_elem, b_box = pairs[j]
            area = a_box.overlap_area(b_box)
            if area > threshold:
                overlaps.append((a_elem, b_elem, area))
    return overlaps


def _label(elem: etree._Element) -> str:
    eid = elem.get("id")
    if eid:
        return f"#{eid}"
    text = (elem.text or "").strip()[:30]
    return f"<text>{text!r}"


def render_overlay(
    svg_path: Path,
    out_path: Path,
    overlaps: list[tuple[etree._Element, etree._Element, float]],
    pairs: list[tuple[etree._Element, Bbox]],
) -> None:
    """Copy the SVG and overlay red rects on each colliding text bbox."""
    tree = etree.parse(str(svg_path))
    root = tree.getroot()
    bbox_by_elem = {id(e): b for e, b in pairs}
    flagged: set[int] = set()
    for a, b, _ in overlaps:
        flagged.add(id(a))
        flagged.add(id(b))
    for elem_id_int in flagged:
        bbox = bbox_by_elem.get(elem_id_int)
        if bbox is None:
            continue
        rect = etree.SubElement(
            root, f"{SVG_TAG}rect",
            attrib={
                "x": str(bbox.x), "y": str(bbox.y),
                "width": str(bbox.w), "height": str(bbox.h),
                "fill": "none", "stroke": "#ff0033",
                "stroke-width": "1.5", "stroke-dasharray": "4,2",
                "class": "text-collision-overlay",
            },
        )
        # Add a subtle title for tooltip in browsers.
        etree.SubElement(rect, f"{SVG_TAG}title").text = "text bbox collision"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out_path), xml_declaration=True, encoding="utf-8", pretty_print=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect overlapping <text> bboxes in an SVG figure",
    )
    parser.add_argument("svg", type=Path, help="path to the SVG to check")
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="minimum overlap area (in viewBox units²) to report (default: 0)")
    parser.add_argument("--render-overlay", type=Path, default=None,
                        help="optional output SVG with red dashed rectangles drawn on every "
                             "colliding text bbox")
    parser.add_argument("--no-inkscape", action="store_true",
                        help="skip the Inkscape exact-bbox query and use the heuristic only "
                             "(faster + works without Inkscape installed)")
    args = parser.parse_args()

    if not args.svg.exists():
        print(f"Error: SVG not found: {args.svg}", file=sys.stderr)
        return 2

    try:
        pairs = collect_text_bboxes(args.svg, use_inkscape=not args.no_inkscape)
    except (etree.XMLSyntaxError, etree.ParseError, OSError) as exc:
        # Convert lxml exceptions into a stable CLI error code so callers
        # (CI gates, the assemble_figure post-step) get a predictable
        # signal instead of a Python traceback.
        print(f"Error: failed to parse SVG: {exc}", file=sys.stderr)
        return 2
    overlaps = find_overlaps(pairs, threshold=args.threshold)

    if not overlaps:
        print(f"OK: no <text> bbox overlaps in {args.svg.name} "
              f"(checked {len(pairs)} text elements, threshold={args.threshold})")
        return 0

    print(f"FOUND {len(overlaps)} <text> bbox overlap(s) in {args.svg.name}:",
          file=sys.stderr)
    for a, b, area in sorted(overlaps, key=lambda t: -t[2]):
        print(f"  {_label(a)} ⨯ {_label(b)}  overlap={area:.1f} units²",
              file=sys.stderr)

    if args.render_overlay:
        try:
            render_overlay(args.svg, args.render_overlay, overlaps, pairs)
        except OSError as exc:
            # Same CI-friendly contract as the parse-failure branch
            # above: never let a write failure dump a traceback.
            print(f"Error: failed to write overlay {args.render_overlay}: {exc}",
                  file=sys.stderr)
            return 2
        print(f"\nOverlay written to: {args.render_overlay}", file=sys.stderr)

    return 1  # non-zero so CI can gate on it


if __name__ == "__main__":
    sys.exit(main())
