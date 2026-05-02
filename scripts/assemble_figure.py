#!/usr/bin/env python3
"""
assemble_figure.py — fill an SVG template from a manifest, normalize fonts,
inject CC-BY attribution, and export SVG / PDF / PPTX.

Workflow:
  template SVG  +  manifest.yaml  ->  filled SVG  ->  PDF / PPTX

  manifest.panels: { placeholder-id : element-id-or-relative-path }
  manifest.labels: { text-element-id : "string content" }

Dependencies:
  - lxml, pyyaml         (always)
  - Inkscape >= 1.2 CLI  (PDF / PNG export)
  - python-pptx          (PPTX export, optional)

Usage:
  python assemble_figure.py \
      --template templates/dark_proteome_4panel_template.svg \
      --manifest templates/dark_proteome_manifest.yaml \
      --output ./fig1.svg \
      --export-pdf --export-pptx
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml
from lxml import etree


SVG_NS = {
    "svg": "http://www.w3.org/2000/svg",
    "xlink": "http://www.w3.org/1999/xlink",
}
SVG_TAG = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


# ---------------------------------------------------------------------------
# Manifest + element resolution
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: Path) -> dict:
    """Load the YAML manifest as a plain dict.

    yaml.safe_load returns None for an empty / comment-only file; coerce to
    an empty dict so downstream `manifest.get(...)` calls don't blow up with
    AttributeError.
    """
    return yaml.safe_load(manifest_path.read_text()) or {}


# Same shape that library_tools.load_unified_index() emits for legacy YAML rows.
_LEGACY_CATEGORIES = (
    "cells", "molecules", "organelles", "tissues",
    "organs", "equipment", "pathways", "arrows",
)


def _scan_legacy_yaml(value: str, library_root: Path) -> dict | None:
    """Walk per-category _index.yaml files for a `manual-...` style ID.

    library_tools.search emits IDs like `manual-cells-treg-flat-v1` for rows
    registered through the legacy YAML path; without this fallback, those IDs
    can't be resolved here, even though the same ID renders in `search`.
    """
    if not value.startswith("manual-"):
        return None
    for category in _LEGACY_CATEGORIES:
        yaml_index = library_root / category / "_index.yaml"
        if not yaml_index.exists():
            continue
        try:
            data = yaml.safe_load(yaml_index.read_text()) or {"elements": []}
        except Exception:
            continue
        for entry in data.get("elements", []):
            file_stem = (entry.get("file") or "").replace(".svg", "")
            fid = f"manual-{category}-{file_stem}"
            if fid == value:
                return {
                    "id": fid,
                    "_category": category,
                    "_file": entry.get("file", ""),
                    "license": entry.get("license", "research-use-only"),
                    "source_name": entry.get("provider", "manual"),
                    "attribution_required": False,
                }
    return None


def resolve_element_path(value: str, library_root: Path) -> Path:
    """Map a manifest value to an actual on-disk file.

    Disambiguation:
      * "/" in value or extension in {.svg, .png, .emf}  ->  treated as a
        path relative to `library_root`.
      * starts with "manual-"                             ->  legacy YAML lookup.
      * else                                              ->  unified-index ID
        lookup in library_root/library/index.json.
    """
    if "/" in value or value.lower().endswith((".svg", ".png", ".emf")):
        return library_root / value

    legacy = _scan_legacy_yaml(value, library_root)
    if legacy is not None:
        return library_root / legacy["_category"] / legacy["_file"]

    json_index = library_root / "library" / "index.json"
    if json_index.exists():
        try:
            records: list[dict] = json.loads(json_index.read_text())
            for r in records:
                if r.get("id") == value:
                    return library_root / "library" / r["file"]
        except Exception:
            pass
    return library_root / value


def lookup_attribution(value: str, library_root: Path) -> dict | None:
    """If `value` references a CC-BY element, return its record.

    Resolves both shapes:
      * unified-index ID  ->  match `r["id"] == value`
      * relative path     ->  match `<library_root>/library/<r["file"]>`
                              against the resolved manifest path; this covers
                              the case where a user references a Reactome /
                              Servier asset by path instead of ID.

    Walks the unified `library/index.json` and the legacy per-category YAMLs.
    Legacy YAML rows aren't currently flagged attribution_required, but the
    symmetric scan keeps the resolution model consistent if that changes.
    """
    json_index = library_root / "library" / "index.json"

    # ID branch.
    if not ("/" in value or value.lower().endswith((".svg", ".png", ".emf"))):
        if json_index.exists():
            try:
                for r in json.loads(json_index.read_text()):
                    if r.get("id") == value and r.get("attribution_required"):
                        return r
            except Exception:
                pass
        legacy = _scan_legacy_yaml(value, library_root)
        if legacy and legacy.get("attribution_required"):
            return legacy
        return None

    # Path branch: resolve and reverse-lookup against the unified index by
    # comparing against the on-disk path each record points at.
    target = (library_root / value).resolve()
    if json_index.exists():
        try:
            for r in json.loads(json_index.read_text()):
                if not r.get("attribution_required"):
                    continue
                rec_path = (library_root / "library" / r["file"]).resolve()
                if rec_path == target:
                    return r
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Template filling
# ---------------------------------------------------------------------------


def _find_placeholders(root: etree._Element, panel_id: str) -> list[etree._Element]:
    """Return SVG nodes whose `data-placeholder` attr equals `panel_id`.

    Uses lxml's `findall` with attribute predicate to avoid dynamic xpath
    string injection from manifest keys.
    """
    return root.findall(
        f'.//svg:*[@data-placeholder="{panel_id}"]',
        namespaces=SVG_NS,
    ) if not _suspicious_id(panel_id) else []


def _suspicious_id(panel_id: str) -> bool:
    """Reject IDs containing chars that could break the xpath literal."""
    return any(c in panel_id for c in ('"', "\n", "\r"))


def fill_placeholders(template_path: Path, manifest: dict, output_path: Path) -> None:
    """Write a filled SVG with element bodies + label text + attribution caption."""
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(template_path), parser)
    root = tree.getroot()

    library_root = Path(
        manifest.get("library_root", "~/sci-illustration-library")
    ).expanduser()

    used_attribution: list[dict] = []

    # 1. Element placeholders.
    for panel_id, element_value in (manifest.get("panels") or {}).items():
        if _suspicious_id(panel_id):
            print(f"Warning: rejecting suspicious placeholder id: {panel_id!r}",
                  file=sys.stderr)
            continue

        element_full_path = resolve_element_path(element_value, library_root)
        if not element_full_path.exists():
            print(f"Warning: element not found: {element_full_path} "
                  f"(value='{element_value}')", file=sys.stderr)
            continue

        attr_record = lookup_attribution(element_value, library_root)
        if attr_record is not None:
            used_attribution.append(attr_record)

        placeholders = _find_placeholders(root, panel_id)
        if not placeholders:
            print(f"Warning: placeholder '{panel_id}' not in template", file=sys.stderr)
            continue

        if element_full_path.suffix.lower() == ".svg":
            try:
                elem_tree = etree.parse(str(element_full_path))
                elem_root = elem_tree.getroot()
            except Exception as exc:
                print(f"Warning: failed to parse {element_full_path}: {exc}",
                      file=sys.stderr)
                continue

            # `_find_placeholders` may return multiple hits for one panel_id;
            # suffix the wrapper id with the 1-based index so the output SVG
            # never has duplicate ids (which break selectors / a11y / editor
            # validation).
            for idx, placeholder in enumerate(placeholders, start=1):
                parent = placeholder.getparent()
                if parent is None:
                    continue
                wrapper = etree.SubElement(
                    parent, f"{SVG_TAG}g",
                    attrib={
                        "id": f"panel-{panel_id}-{idx}",
                        "transform": placeholder.get("transform", ""),
                    },
                )
                # Deep-copy each child so two placeholders sharing one source
                # SVG don't share node identity.
                for child in elem_root:
                    wrapper.append(copy.deepcopy(child))
                parent.remove(placeholder)
        else:
            # Use a file:// URI rather than a bare filesystem path so the
            # href stays valid across spaces in path components, Windows
            # drive letters, and `xmllint`/browser SVG renderers that treat
            # naked paths as relative.
            href_uri = element_full_path.resolve().as_uri()
            for idx, placeholder in enumerate(placeholders, start=1):
                parent = placeholder.getparent()
                if parent is None:
                    continue
                etree.SubElement(
                    parent, f"{SVG_TAG}image",
                    attrib={
                        "id": f"panel-{panel_id}-{idx}",
                        "x": placeholder.get("x", "0"),
                        "y": placeholder.get("y", "0"),
                        "width": placeholder.get("width", "100"),
                        "height": placeholder.get("height", "100"),
                        XLINK_HREF: href_uri,
                    },
                )
                parent.remove(placeholder)

        print(f"[fill] panel {panel_id} <- {element_value}")

    # 2. Text labels.
    for label_id, text_content in (manifest.get("labels") or {}).items():
        if _suspicious_id(label_id):
            print(f"Warning: rejecting suspicious label id: {label_id!r}", file=sys.stderr)
            continue
        text_elems = root.findall(
            f'.//svg:text[@id="{label_id}"]',
            namespaces=SVG_NS,
        )
        if not text_elems:
            print(f"Warning: label '{label_id}' not in template", file=sys.stderr)
            continue
        for t in text_elems:
            for child in list(t):
                t.remove(child)
            t.text = str(text_content)
            print(f"[fill] label {label_id} <- '{text_content}'")

    # 3. Attribution caption.
    if used_attribution:
        attr_lines = sorted({
            r.get("attribution") or f"{r.get('source_name', '')} ({r.get('license', '')})"
            for r in used_attribution
        })
        attr_text = "Adapted from: " + "; ".join(attr_lines)
        # Per SVG spec, viewBox values are separated by whitespace, comma, or
        # both (e.g. "0, 0, 1600, 1000" is valid). `.split()` only handles
        # whitespace and would either silently fall back to defaults or, worse,
        # raise ValueError on `float("1600,")`. Use a comma+whitespace regex.
        viewbox_raw = (root.get("viewBox") or "0 0 1600 1000").strip()
        viewbox = [v for v in re.split(r"[\s,]+", viewbox_raw) if v]
        canvas_w = float(viewbox[2]) if len(viewbox) >= 4 else 1600.0
        canvas_h = float(viewbox[3]) if len(viewbox) >= 4 else 1000.0
        attr_node = etree.SubElement(
            root, f"{SVG_TAG}text",
            attrib={
                "x": str(canvas_w - 10),
                "y": str(canvas_h - 5),
                "text-anchor": "end",
                "font-size": "8",
                "fill": "#888",
                "font-family": "Arial",
                "class": "attribution",
            },
        )
        attr_node.text = attr_text
        print(f"[attribution] injected: {attr_text[:80]}...")

    tree.write(str(output_path), xml_declaration=True,
               encoding="utf-8", pretty_print=True)
    print(f"[fill] saved: {output_path}")


# ---------------------------------------------------------------------------
# Font normalization & exports
# ---------------------------------------------------------------------------


def normalize_fonts(svg_path: Path, font_family: str) -> None:
    """Force every text-bearing element to use one font-family.

    Three passes:
      1. set font-family on the root <svg> so inheritance picks it up;
      2. rewrite any explicit `font-family=...` attribute or `style=...;font-family:...`;
      3. for `<text>` / `<tspan>` that declare neither, set an explicit
         `font-family` so merged CC0 subtrees (whose own <svg> root we
         dropped) don't fall back to the UA default.
    """
    tree = etree.parse(str(svg_path))
    root = tree.getroot()

    count = 0
    # (1) root inheritance.
    if root.get("font-family") != font_family:
        root.set("font-family", font_family)
        count += 1

    text_tags = {f"{SVG_TAG}text", f"{SVG_TAG}tspan"}
    for elem in root.iter():
        # (2) rewrite explicit attribute.
        if elem.get("font-family") and elem.get("font-family") != font_family:
            elem.set("font-family", font_family)
            count += 1

        # (2) rewrite within style="...".
        style = elem.get("style", "")
        if "font-family" in style:
            parts: list[str] = []
            for chunk in style.split(";"):
                if chunk.strip().startswith("font-family"):
                    parts.append(f"font-family:{font_family}")
                else:
                    parts.append(chunk)
            elem.set("style", ";".join(parts))
            count += 1

        # (3) explicitly set on text-bearing elements that have nothing.
        if (elem.tag in text_tags
                and not elem.get("font-family")
                and "font-family" not in (elem.get("style") or "")):
            elem.set("font-family", font_family)
            count += 1

    tree.write(str(svg_path), xml_declaration=True, encoding="utf-8")
    print(f"[font] normalized {count} elements -> {font_family}")


def _run_inkscape(cmd: list[str], purpose: str) -> bool:
    """Run an Inkscape CLI invocation; return False (don't crash) on failure."""
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except FileNotFoundError:
        print(f"Warning: Inkscape CLI not found on PATH; skipping {purpose}.",
              file=sys.stderr)
        print("  Install Inkscape >= 1.2 from https://inkscape.org/", file=sys.stderr)
        return False
    except subprocess.CalledProcessError as exc:
        stderr_tail = (exc.stderr or b"").decode("utf-8", errors="replace")[-400:]
        print(f"Warning: Inkscape failed during {purpose}: {exc.returncode}",
              file=sys.stderr)
        if stderr_tail.strip():
            print(f"  stderr: {stderr_tail.strip()}", file=sys.stderr)
        return False


def export_pdf(svg_path: Path, pdf_path: Path, outline_text: bool = False) -> None:
    """Export PDF via Inkscape; for submission, set outline_text=True."""
    cmd = [
        "inkscape", str(svg_path),
        "--export-type=pdf",
        "--export-pdf-version=1.5",
        f"--export-text-to-path={'true' if outline_text else 'false'}",
        f"--export-filename={pdf_path}",
    ]
    if _run_inkscape(cmd, f"PDF export -> {pdf_path}"):
        print(f"[export] PDF: {pdf_path}")


def export_pptx(svg_path: Path, pptx_path: Path) -> None:
    """Render the SVG to a high-DPI PNG and embed into a 16:9 PPTX slide."""
    try:
        from pptx import Presentation
        from pptx.util import Inches
    except ImportError:
        print("Warning: python-pptx not installed; skipping PPTX export.",
              file=sys.stderr)
        print("  pip install python-pptx", file=sys.stderr)
        return

    png_temp = svg_path.with_suffix(".tmp.png")
    rendered = _run_inkscape(
        [
            "inkscape", str(svg_path),
            "--export-type=png",
            "--export-dpi=300",
            f"--export-filename={png_temp}",
        ],
        "PNG raster for PPTX",
    )
    if not rendered:
        return

    try:
        prs = Presentation()
        prs.slide_width = Inches(13.333)  # 16:9
        prs.slide_height = Inches(7.5)
        blank_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(blank_layout)
        slide.shapes.add_picture(
            str(png_temp),
            left=Inches(0.5), top=Inches(0.5),
            width=Inches(12.333), height=Inches(6.5),
        )
        prs.save(str(pptx_path))
        print(f"[export] PPTX: {pptx_path}")
    finally:
        png_temp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Template-driven scientific figure assembler")
    parser.add_argument("--template", type=Path, required=True, help="SVG template")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest YAML")
    parser.add_argument("--output", type=Path, required=True, help="output SVG path")
    parser.add_argument("--font", default="Arial", help="unified font-family (default: Arial)")
    parser.add_argument("--export-pdf", action="store_true",
                        help="export an editable PDF alongside the SVG")
    parser.add_argument("--export-pdf-final", action="store_true",
                        help="export submission PDF with text converted to paths")
    parser.add_argument("--export-pptx", action="store_true",
                        help="export a 16:9 PPTX with the rendered figure embedded")
    args = parser.parse_args()

    if not args.template.exists():
        print(f"Error: template not found: {args.template}", file=sys.stderr)
        return 1
    if not args.manifest.exists():
        print(f"Error: manifest not found: {args.manifest}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.manifest)

    fill_placeholders(args.template, manifest, args.output)
    normalize_fonts(args.output, args.font)

    if args.export_pdf:
        export_pdf(
            args.output,
            args.output.with_name(args.output.stem + "_editable.pdf"),
            outline_text=False,
        )
    if args.export_pdf_final:
        export_pdf(
            args.output,
            args.output.with_name(args.output.stem + "_submission.pdf"),
            outline_text=True,
        )
    if args.export_pptx:
        export_pptx(args.output, args.output.with_suffix(".pptx"))

    print(f"\nAll done. Main output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
