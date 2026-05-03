#!/usr/bin/env python3
"""
lowfi_trace.py — normalize element style across a multi-source CC0 library.

The bootstrap phase pulls SVGs from heterogeneous sources (BioIcons,
PhyloPic, Reactome, SciDraw, Servier, Recraft, GPT-Image-2 outline) — each
with its own line weight, palette, abstraction level. Mixing them
side-by-side in a 4-panel figure looks like a Frankenstein collage.

This script does the v0.2 "low-fidelity tracing" pass the user proposed
on 2026-05-03 (issue #2): for each input SVG, normalize:

  - **stroke-width** — every stroke clamped to a single project value
  - **palette** — every fill/stroke color snapped to the nearest swatch
    in the project palette
  - **gradients** — collapsed to flat fills (mid-stop color)
  - **transforms** — flattened where simple (translate/scale only)
  - optional: simplify path detail via Inkscape

Output: a parallel `_normalized/` tree with the same relative paths.
Original elements are left untouched so the operation is repeatable.

Usage:
  python lowfi_trace.py --library-root ~/sci-illustration-library \
                        --palette _palettes/nature-flat-blue.yaml \
                        --stroke-width 1.5
  # then point assemble_figure.py at <library_root>/library_normalized/

Prefer `--dry-run` first to see how many elements would change without
touching the disk.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from lxml import etree


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
SVG_TAG = "{http://www.w3.org/2000/svg}"

# Module-global, set by main() via --library-root. Functions take it as
# an arg so they can be unit-tested without touching the global.
DEFAULT_LIBRARY_ROOT = Path.home() / "sci-illustration-library"


# ---------------------------------------------------------------------------
# Palette loading + nearest-color
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Color:
    r: int
    g: int
    b: int

    @classmethod
    def from_hex(cls, s: str) -> "Color | None":
        s = s.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) != 6:
            return None
        try:
            return cls(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return None

    def to_hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    def distance_sq(self, other: "Color") -> int:
        return (self.r - other.r) ** 2 + (self.g - other.g) ** 2 + (self.b - other.b) ** 2


def load_palette(palette_path: Path | None) -> list[Color]:
    """Load a palette from YAML; return [] (no snapping) if path is None.

    Schema:
      colors:
        - "#1B5BA0"
        - "#2E7CD6"
        - ...
    """
    if palette_path is None:
        return []
    try:
        data = yaml.safe_load(palette_path.read_text()) or {}
    except (yaml.YAMLError, OSError) as exc:
        print(f"[error] failed to load palette {palette_path}: {exc}", file=sys.stderr)
        return []
    raw = data.get("colors") or []
    out: list[Color] = []
    for item in raw:
        c = Color.from_hex(str(item))
        if c is not None:
            out.append(c)
    return out


def snap_to_palette(value: str, palette: list[Color]) -> str:
    """Snap a single SVG color value to the nearest palette swatch.

    Pass through `none` / `transparent` / non-color strings (e.g.
    `url(#grad)`) unchanged. Recognized inputs are 3- and 6-hex.
    """
    if not palette:
        return value
    s = value.strip().lower()
    if s in ("none", "transparent", "currentcolor", "inherit") or s.startswith("url"):
        return value
    src = Color.from_hex(s)
    if src is None:
        return value
    nearest = min(palette, key=lambda c: src.distance_sq(c))
    return nearest.to_hex()


# ---------------------------------------------------------------------------
# Element normalization
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    files_seen: int = 0
    files_normalized: int = 0
    strokes_clamped: int = 0
    colors_snapped: int = 0
    gradients_flattened: int = 0


def _flatten_gradient_to_flat_fill(
    root: etree._Element,
    gradient_id_to_color: dict[str, str],
) -> int:
    """Replace every `url(#gradient-id)` reference with the gradient's mid-stop color.

    Walks both the explicit `fill` / `stroke` attributes AND the inline
    `style="fill:url(#…); stroke:url(#…)"` form that Inkscape/Affinity emit;
    without the style pass, gradient references baked into `style=` survive
    the flatten and the downstream palette-snap can't reach them.
    """
    flattened = 0
    for elem in root.iter():
        for attr in ("fill", "stroke"):
            val = elem.get(attr) or ""
            if val.startswith("url(#"):
                gid = val[5:].rstrip(")").strip("\"' ")
                if gid in gradient_id_to_color:
                    elem.set(attr, gradient_id_to_color[gid])
                    flattened += 1
        style = elem.get("style") or ""
        if "url(#" in style:
            new_chunks: list[str] = []
            style_changed = False
            for chunk in style.split(";"):
                key, sep, val = chunk.partition(":")
                key_clean = key.strip().lower()
                val_clean = val.strip()
                if (key_clean in ("fill", "stroke")
                        and val_clean.startswith("url(#")):
                    gid = val_clean[5:].rstrip(")").strip("\"' ")
                    if gid in gradient_id_to_color:
                        new_chunks.append(f"{key.strip()}:{gradient_id_to_color[gid]}")
                        flattened += 1
                        style_changed = True
                        continue
                new_chunks.append(chunk)
            if style_changed:
                elem.set("style", ";".join(new_chunks))
    return flattened


def _gradient_mid_color(grad_elem: etree._Element) -> str | None:
    """Pick the middle stop's color from a `<linearGradient>` / `<radialGradient>`."""
    stops = grad_elem.findall(".//svg:stop", SVG_NS)
    if not stops:
        return None
    mid = stops[len(stops) // 2]
    color = mid.get("stop-color")
    if color:
        return color
    style = mid.get("style") or ""
    for chunk in style.split(";"):
        if "stop-color" in chunk:
            return chunk.split(":", 1)[-1].strip()
    return None


def normalize_one(
    svg_path: Path,
    out_path: Path,
    *,
    palette: list[Color],
    stroke_width_pt: float | None,
    flatten_gradients: bool,
    stats: Stats,
) -> bool:
    """Normalize one SVG; write to `out_path`. Return True iff anything changed."""
    try:
        tree = etree.parse(str(svg_path))
    except (etree.XMLSyntaxError, etree.ParseError, OSError) as exc:
        print(f"[skip] {svg_path}: {exc}", file=sys.stderr)
        return False
    root = tree.getroot()
    changed = False

    # 1. Flatten gradients first so the subsequent color-snap pass picks up
    #    the inserted flat-fill values.
    if flatten_gradients:
        gradient_ids: dict[str, str] = {}
        for tag in (f"{SVG_TAG}linearGradient", f"{SVG_TAG}radialGradient"):
            for grad in root.iter(tag):
                gid = grad.get("id")
                if not gid:
                    continue
                col = _gradient_mid_color(grad)
                if col:
                    gradient_ids[gid] = col
        if gradient_ids:
            flat = _flatten_gradient_to_flat_fill(root, gradient_ids)
            if flat:
                stats.gradients_flattened += flat
                changed = True

    # 2. Snap fill / stroke colors to nearest palette entry.
    if palette:
        for elem in root.iter():
            for attr in ("fill", "stroke", "stop-color"):
                val = elem.get(attr)
                if val:
                    new = snap_to_palette(val, palette)
                    if new != val:
                        elem.set(attr, new)
                        stats.colors_snapped += 1
                        changed = True
            # also handle `style="fill:#…;stroke:#…"`
            style = elem.get("style") or ""
            if style and (":#" in style or "rgb" in style):
                new_style = _snap_style_colors(style, palette)
                if new_style != style:
                    elem.set("style", new_style)
                    stats.colors_snapped += 1
                    changed = True

    # 3. Clamp stroke widths. Cover both `stroke-width="..."` attributes
    #    and inline `style="stroke-width:..."`, since Inkscape/Affinity
    #    routinely emit the latter.
    if stroke_width_pt is not None:
        target = str(stroke_width_pt)
        for elem in root.iter():
            sw = elem.get("stroke-width")
            if sw and sw != target:
                elem.set("stroke-width", target)
                stats.strokes_clamped += 1
                changed = True
            style = elem.get("style") or ""
            if "stroke-width" in style:
                new_chunks: list[str] = []
                style_changed = False
                for chunk in style.split(";"):
                    key, sep, val = chunk.partition(":")
                    if (key.strip().lower() == "stroke-width"
                            and val.strip() != target):
                        new_chunks.append(f"{key.strip()}:{target}")
                        style_changed = True
                        continue
                    new_chunks.append(chunk)
                if style_changed:
                    elem.set("style", ";".join(new_chunks))
                    stats.strokes_clamped += 1
                    changed = True

    if changed:
        # Tolerate write failures the same way the read path does — one
        # bad path/permission on a single file shouldn't kill the whole
        # batch (the caller's `walk_library` continues on a False return).
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tree.write(str(out_path), xml_declaration=True, encoding="utf-8")
        except OSError as exc:
            print(f"[skip] {svg_path}: failed to write {out_path}: {exc}",
                  file=sys.stderr)
            return False
    return changed


def _snap_style_colors(style: str, palette: list[Color]) -> str:
    """Walk a `style="..."` value and snap each color to the palette."""
    out_chunks: list[str] = []
    for chunk in style.split(";"):
        if ":" in chunk:
            key, _, val = chunk.partition(":")
            key_clean = key.strip().lower()
            if key_clean in ("fill", "stroke", "stop-color"):
                snapped = snap_to_palette(val.strip(), palette)
                out_chunks.append(f"{key.strip()}:{snapped}")
                continue
        out_chunks.append(chunk)
    return ";".join(out_chunks)


# ---------------------------------------------------------------------------
# Library walker
# ---------------------------------------------------------------------------


def walk_library(
    library_root: Path,
    output_root: Path,
    *,
    palette: list[Color],
    stroke_width_pt: float | None,
    flatten_gradients: bool,
    dry_run: bool,
) -> Stats:
    """Walk every `library/**/*.svg` and write normalized copies to `output_root`."""
    stats = Stats()
    src_dir = library_root / "library"
    if not src_dir.is_dir():
        print(f"[error] library directory not found: {src_dir}", file=sys.stderr)
        return stats

    for svg in src_dir.rglob("*.svg"):
        stats.files_seen += 1
        rel = svg.relative_to(src_dir)
        out = output_root / rel
        if dry_run:
            # Render to /dev/null to count what *would* change.
            tmp = Path("/dev/null") if sys.platform != "win32" else Path("nul")
            try:
                changed = normalize_one(
                    svg, tmp,
                    palette=palette,
                    stroke_width_pt=stroke_width_pt,
                    flatten_gradients=flatten_gradients,
                    stats=stats,
                )
            except OSError:
                # Can't actually write to /dev/null on some setups; just count
                # everything as "would normalize".
                changed = True
            if changed:
                stats.files_normalized += 1
            continue
        changed = normalize_one(
            svg, out,
            palette=palette,
            stroke_width_pt=stroke_width_pt,
            flatten_gradients=flatten_gradients,
            stats=stats,
        )
        if changed:
            stats.files_normalized += 1
        else:
            # No-op: copy through so the normalized tree is complete and the
            # downstream `library_root_normalized` is a 1:1 mirror. Tolerate
            # a single copy failure (permissions / disk full) and continue.
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(svg, out)
            except OSError as exc:
                print(f"[skip] {svg}: failed to copy to {out}: {exc}",
                      file=sys.stderr)
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Low-fidelity style normalization across the CC0 element library "
                    "(stroke clamp, palette snap, gradient flatten).",
    )
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT,
                        help=f"library root (default: {DEFAULT_LIBRARY_ROOT})")
    parser.add_argument("--output", type=Path, default=None,
                        help="output root (default: <library-root>/library_normalized)")
    parser.add_argument("--palette", type=Path, default=None,
                        help="YAML with `colors:` list of hex strings; passes "
                             "through colors unchanged when omitted")
    parser.add_argument("--stroke-width", type=float, default=None,
                        help="clamp every stroke-width to this value (in viewBox units; "
                             "rule of thumb 1.0–2.0 for printable figures)")
    parser.add_argument("--no-flatten-gradients", action="store_true",
                        help="leave <linearGradient>/<radialGradient> references "
                             "alone; default flattens them to mid-stop flat fill")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing the output tree")
    args = parser.parse_args()

    library_root = args.library_root.expanduser().resolve()
    output_root = (
        args.output.expanduser().resolve()
        if args.output
        else library_root / "library_normalized"
    )
    # Refuse to write into <library-root>/library, otherwise rglob() on the
    # next run would re-pick up our own output and the library would
    # recursively bloat (or two passes would normalize their own outputs).
    src_dir = (library_root / "library").resolve()
    try:
        output_root.relative_to(src_dir)
    except ValueError:
        pass
    else:
        print(f"Error: --output ({output_root}) must not be inside the source "
              f"library directory ({src_dir}); pick a sibling like "
              f"{library_root / 'library_normalized'} instead.",
              file=sys.stderr)
        return 1

    palette = load_palette(args.palette.expanduser() if args.palette else None)

    print(f"library:  {library_root / 'library'}")
    print(f"output:   {output_root}")
    print(f"palette:  {len(palette)} swatches"
          f"{' (no snap)' if not palette else ''}")
    print(f"stroke:   {args.stroke_width}"
          f"{' (no clamp)' if args.stroke_width is None else ''}")
    print(f"gradients: {'leave-as-is' if args.no_flatten_gradients else 'flatten'}")
    print(f"mode:     {'DRY-RUN' if args.dry_run else 'WRITE'}\n")

    stats = walk_library(
        library_root, output_root,
        palette=palette,
        stroke_width_pt=args.stroke_width,
        flatten_gradients=not args.no_flatten_gradients,
        dry_run=args.dry_run,
    )

    print(f"\nWalked:    {stats.files_seen} svg files")
    print(f"Changed:   {stats.files_normalized}"
          f" ({stats.files_normalized * 100 // max(stats.files_seen, 1)}%)")
    print(f"  - colors snapped:    {stats.colors_snapped}")
    print(f"  - strokes clamped:   {stats.strokes_clamped}")
    print(f"  - gradients flattened:{stats.gradients_flattened}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
