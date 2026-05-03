#!/usr/bin/env python3
"""
library_build_static.py — generate a self-contained HTML site for the library.

Sister of `library_tools.py preview --html` (which is per-search). This
script is the **catalog** view: walk the whole `library/index.json`, render
a thumbnail per element, and emit a static HTML page with client-side JS
filters (source / category / license / attribution) plus a full-text search
box. Open the resulting `index.html` directly in a browser — no server,
no deploy.

Usage:
  python scripts/library_build_static.py \
      --library-root ~/sci-illustration-library \
      --output ~/sci-illustration-library/_browse
  open ~/sci-illustration-library/_browse/index.html

By default the catalog is built into `<library_root>/_browse/`. Thumbnails
are written into `<output>/thumbs/<id>.png` so the HTML stays small (one
file per element instead of huge data-URIs everywhere). Pass
`--inline-thumbs` to fall back to data-URIs for true single-file output.
"""

from __future__ import annotations

import argparse
import base64
import html as html_lib
import json
import sys
from io import BytesIO
from pathlib import Path

try:
    import cairosvg
    HAS_CAIROSVG = True
except (ImportError, OSError):
    HAS_CAIROSVG = False

try:
    from PIL import Image
    HAS_PILLOW = True
except (ImportError, OSError):
    HAS_PILLOW = False


def _render_thumb_bytes(svg_path: Path, cell: int) -> bytes | None:
    """Render `svg_path` to a PNG byte string of size `cell`×`cell`. None on failure."""
    ext = svg_path.suffix.lower()
    try:
        if ext == ".svg":
            if not HAS_CAIROSVG:
                return None
            return cairosvg.svg2png(
                url=str(svg_path), output_width=cell, output_height=cell,
            )
        if ext in (".png", ".jpg", ".jpeg"):
            if not HAS_PILLOW:
                return None
            img = Image.open(svg_path).convert("RGB")
            img.thumbnail((cell, cell))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except (OSError, ValueError):
        return None
    return None


def _build_catalog_records(records: list[dict], library_root: Path,
                           output_dir: Path, *,
                           cell: int, inline_thumbs: bool) -> list[dict]:
    """Render thumbnails + emit a serializable record list for the JS catalog."""
    thumbs_dir = output_dir / "thumbs"
    if not inline_thumbs:
        thumbs_dir.mkdir(parents=True, exist_ok=True)

    catalog: list[dict] = []
    for r in records:
        # Resolve path the same way assemble_figure / library_tools do.
        root = r.get("_root", library_root / "library")
        svg_path = Path(root) / r.get("file", "")
        download_uri = svg_path.resolve().as_uri() if svg_path.exists() else ""

        thumb_data = _render_thumb_bytes(svg_path, cell) if svg_path.exists() else None
        if inline_thumbs and thumb_data:
            thumb_uri = "data:image/png;base64," + base64.b64encode(thumb_data).decode()
        elif thumb_data:
            thumb_path = thumbs_dir / f"{_safe_id(r['id'])}.png"
            thumb_path.write_bytes(thumb_data)
            thumb_uri = f"thumbs/{thumb_path.name}"
        else:
            thumb_uri = ""

        catalog.append({
            "id":       r.get("id", ""),
            "name":     r.get("name", ""),
            "category": r.get("category", "") or "uncategorized",
            "source":   r.get("source", "") or "?",
            "license":  r.get("license", "") or "?",
            "attr_required": bool(r.get("attribution_required")),
            "tags":     r.get("tags", []),
            "thumb":    thumb_uri,
            "download": download_uri,
        })
    return catalog


def _safe_id(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


# Inlined HTML template (single file so the script ships as one .py).
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CC0 Library Catalog · {n} elements</title>
<style>
  :root {{
    --fg: #222; --muted: #666; --bg: #fafafa; --card: #fff;
    --accent: #2E7CD6; --attr: #C8A431; --warn: #A32D2D;
    --border: #e0e0e0;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
          margin: 0; padding: 1.5rem; background: var(--bg); color: var(--fg); }}
  header {{ position: sticky; top: 0; z-index: 10; background: var(--bg);
            padding: 0.75rem 0; border-bottom: 1px solid var(--border); }}
  h1 {{ font-size: 1.1rem; font-weight: 600; margin: 0 0 0.5rem; color: var(--muted); }}
  .filters {{ display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }}
  .filters input, .filters select {{
    padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px;
    background: white; font-size: 0.85rem; }}
  .filters input[type=search] {{ flex: 1 1 280px; min-width: 240px; }}
  .filters .count {{ color: var(--muted); font-size: 0.85rem; margin-left: auto; }}
  .grid {{ display: grid;
           grid-template-columns: repeat(auto-fill, minmax({cell}px, 1fr));
           gap: 12px; padding-top: 1rem; }}
  .cell {{ background: var(--card); border: 1px solid var(--border);
           border-radius: 6px; padding: 8px; transition: box-shadow .15s ease; }}
  .cell:hover {{ box-shadow: 0 2px 12px rgba(0,0,0,0.08); border-color: #888; }}
  .cell a {{ display: block; text-decoration: none; color: inherit; }}
  .cell img {{ display: block; width: 100%; height: {cell}px; object-fit: contain; background: white; }}
  .cell .missing {{ height: {cell}px; display: flex; align-items: center; justify-content: center;
                    background: #fff7f3; color: var(--warn); font-size: 0.75rem;
                    border: 1px dashed #d4796b; }}
  .name {{ font-weight: 600; font-size: 0.78rem; margin-top: 6px; word-break: break-word; }}
  .meta {{ color: var(--muted); font-size: 0.65rem; }}
  .id   {{ color: #999; font-size: 0.6rem; font-family: ui-monospace, monospace; word-break: break-all; }}
  .attr {{ color: var(--attr); font-weight: 700; }}
  .empty {{ padding: 3rem; text-align: center; color: var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>CC0 Library Catalog · click any thumbnail to download the source file</h1>
  <div class="filters">
    <input type="search" id="q" placeholder="search name / category / id / tags…" autofocus>
    <select id="f-source"><option value="">all sources</option></select>
    <select id="f-category"><option value="">all categories</option></select>
    <select id="f-license"><option value="">all licenses</option></select>
    <label><input type="checkbox" id="f-attr"> CC-BY only</label>
    <span class="count" id="count">0 / 0</span>
  </div>
</header>

<div id="grid" class="grid"></div>
<div id="empty" class="empty" hidden>no matches</div>

<script>
const CATALOG = {catalog_json};

const grid = document.getElementById("grid");
const empty = document.getElementById("empty");
const count = document.getElementById("count");
const qInput = document.getElementById("q");
const fSrc = document.getElementById("f-source");
const fCat = document.getElementById("f-category");
const fLic = document.getElementById("f-license");
const fAttr = document.getElementById("f-attr");

function uniq(values) {{ return [...new Set(values)].sort(); }}
function fillSelect(el, values) {{
  for (const v of values) {{
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    el.appendChild(o);
  }}
}}
fillSelect(fSrc, uniq(CATALOG.map(r => r.source)));
fillSelect(fCat, uniq(CATALOG.map(r => r.category)));
fillSelect(fLic, uniq(CATALOG.map(r => r.license)));

function escapeHtml(s) {{
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}})[c]);
}}

function renderCell(r) {{
  const thumb = r.thumb
    ? `<img src="${{escapeHtml(r.thumb)}}" alt="${{escapeHtml(r.name)}}">`
    : `<div class="missing">missing</div>`;
  const download = r.download
    ? `<a href="${{escapeHtml(r.download)}}" download>${{thumb}}</a>`
    : thumb;
  const attrBadge = r.attr_required ? ' <span class="attr">[ATTR]</span>' : '';
  return `<figure class="cell">
    ${{download}}
    <figcaption>
      <div class="name">${{escapeHtml(r.name)}}</div>
      <div class="meta">${{escapeHtml(r.source)}} · ${{escapeHtml(r.license)}}${{attrBadge}}</div>
      <div class="meta">${{escapeHtml(r.category)}}</div>
      <div class="id">${{escapeHtml(r.id)}}</div>
    </figcaption>
  </figure>`;
}}

function refresh() {{
  const q = qInput.value.trim().toLowerCase();
  const src = fSrc.value;
  const cat = fCat.value;
  const lic = fLic.value;
  const attr = fAttr.checked;

  const filtered = CATALOG.filter(r => {{
    if (src && r.source !== src) return false;
    if (cat && r.category !== cat) return false;
    if (lic && r.license !== lic) return false;
    if (attr && !r.attr_required) return false;
    if (q) {{
      const hay = (r.name + " " + r.category + " " + r.id + " " + (r.tags || []).join(" ")).toLowerCase();
      if (!hay.includes(q)) return false;
    }}
    return true;
  }});

  grid.innerHTML = filtered.slice(0, 2000).map(renderCell).join("");
  empty.hidden = filtered.length > 0;
  count.textContent = `${{filtered.length}} / ${{CATALOG.length}}`;
  if (filtered.length > 2000) {{
    count.textContent += ` (showing first 2000)`;
  }}
}}
[qInput, fSrc, fCat, fLic, fAttr].forEach(el => el.addEventListener("input", refresh));
refresh();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a self-contained HTML catalog for the CC0 library "
                    "(global browse + filter + search).",
    )
    parser.add_argument("--library-root", type=Path,
                        default=Path.home() / "sci-illustration-library",
                        help="library root containing library/index.json")
    parser.add_argument("--output", type=Path, default=None,
                        help="output dir (default: <library-root>/_browse)")
    parser.add_argument("--cell-size", type=int, default=140,
                        help="thumbnail edge px (default: 140)")
    parser.add_argument("--inline-thumbs", action="store_true",
                        help="embed thumbnails as base64 data-URIs in the HTML "
                             "instead of writing thumbs/*.png — produces one "
                             "self-contained but larger HTML file")
    args = parser.parse_args()

    library_root = args.library_root.expanduser().resolve()
    output_dir = (
        args.output.expanduser().resolve()
        if args.output
        else library_root / "_browse"
    )
    index_path = library_root / "library" / "index.json"

    if not index_path.exists():
        print(f"Error: {index_path} not found.", file=sys.stderr)
        print("Run scripts/download_cc0_seed.py first to seed the library.",
              file=sys.stderr)
        return 1

    try:
        records = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error: failed to read {index_path}: {exc}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Building catalog from {len(records)} elements...")
    catalog = _build_catalog_records(
        records, library_root, output_dir,
        cell=args.cell_size, inline_thumbs=args.inline_thumbs,
    )
    rendered = sum(1 for r in catalog if r["thumb"])
    print(f"  rendered {rendered} thumbnails ({len(catalog) - rendered} missing/failed)")

    # `json.dumps` does NOT escape `</`, so a `</script>` substring in any
    # `name` / `tag` / etc. would prematurely close the inlined script tag
    # and leave a local HTML-injection surface. Escape `</` -> `<\/` per
    # the OWASP JSON-in-HTML guidance.
    catalog_json = json.dumps(catalog, ensure_ascii=False).replace("</", "<\\/")
    html = _HTML_TEMPLATE.format(
        n=len(catalog), cell=args.cell_size,
        catalog_json=catalog_json,
    )
    out_html = output_dir / "index.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"\nDone. Open: {out_html}")
    print(f"  or: file://{out_html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
