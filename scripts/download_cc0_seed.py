#!/usr/bin/env python3
"""
download_cc0_seed.py — bootstrap a local scientific icon library from 6 public CC0 / CC-BY / public-domain sources.

Sources:
  1. BioIcons        — CC0          — ~1500 SVG, GitHub repo
  2. PhyloPic v2     — CC0 / CC-BY  — ~12000 silhouettes, REST API
  3. NIH BioArt      — Public Dom.  — ~2500 illustrations, MANUAL ZIP DROP
                                       (the live site is now a Next.js SPA, no server-rendered HTML
                                        and no public REST endpoint, so a HTTP scrape no longer works)
  4. Reactome Icons  — CC BY 4.0    — ~500 SVG, GitHub repo
  5. SciDraw         — CC0          — ~700 SVG, webscrape (Janelia)
  6. Servier Med Art — CC BY 3.0    — ~3000 SVG/EMF, MANUAL PPTX DROP

Dependencies:
  pip install requests beautifulsoup4 lxml tqdm pyyaml
  (python-pptx NOT required: Servier ingestion uses stdlib `zipfile`)

Usage:
  # Run every source that does not need a manual file
  python download_cc0_seed.py --all --target ~/sci-illustration-library

  # One source only
  python download_cc0_seed.py --source bioicons

  # Cap PhyloPic / SciDraw page count
  python download_cc0_seed.py --source phylopic --max-per-source 500

  # Servier requires a manually downloaded PPTX from https://smart.servier.com
  python download_cc0_seed.py --source servier --pptx ./servier-anatomy.pptx

  # NIH BioArt requires a manually downloaded ZIP from
  # https://bioart.niaid.nih.gov/  (use the in-page "Download" buttons)
  python download_cc0_seed.py --source nih_bioart --zip ./nih_bioart.zip

  # Inspect the current library without downloading
  python download_cc0_seed.py --summary-only --target ~/sci-illustration-library

License tracking:
  Every record in `library/index.json` carries a `license` field. CC-BY records
  also get `attribution_required: true` and an `attribution` string, so
  `library_tools.py attribution` can render figure-caption-ready text.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

# Third-party deps are imported lazily by `_require_runtime_deps()` so that
# `--help` and `--summary-only` work even when nothing is installed yet.
requests = None  # type: ignore[assignment]
BeautifulSoup = None  # type: ignore[assignment]
tqdm = None  # type: ignore[assignment]


def _require_runtime_deps() -> None:
    """Import third-party deps and bind them to module globals; fail-fast on miss."""
    global requests, BeautifulSoup, tqdm
    try:
        import requests as _requests
        from bs4 import BeautifulSoup as _BS
        from tqdm import tqdm as _tqdm
    except ImportError as exc:
        print(f"[ERROR] missing dependency: {exc.name}", file=sys.stderr)
        print("        pip install requests beautifulsoup4 lxml tqdm pyyaml", file=sys.stderr)
        sys.exit(1)
    requests = _requests  # type: ignore[assignment]
    BeautifulSoup = _BS  # type: ignore[assignment]
    tqdm = _tqdm  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

SOURCES_META: dict[str, dict] = {
    "bioicons": {
        "name": "BioIcons",
        "url": "https://bioicons.com",
        "repo": "https://github.com/duerrsimon/bioicons.git",
        "license": "CC0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution_required": False,
        "expected_count": 1500,
    },
    "phylopic": {
        "name": "PhyloPic",
        "url": "https://www.phylopic.org",
        "api": "https://api.phylopic.org",
        "license": "CC0/CC-BY",  # per-image
        "license_url": "https://www.phylopic.org/about",
        "attribution_required": "per_image",
        "expected_count": 12000,
    },
    "nih_bioart": {
        "name": "NIH BioArt (NIAID)",
        "url": "https://bioart.niaid.nih.gov",
        "license": "Public Domain",
        "license_url": "https://www.nih.gov/about-nih/nih-image-gallery",
        "attribution_required": False,
        "expected_count": 2500,
        "manual_download": True,
    },
    "reactome": {
        "name": "Reactome Icon Library",
        "url": "https://reactome.org/icon-lib",
        "repo": "https://github.com/reactome/icon-lib.git",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution_required": True,
        "attribution": "Reactome Icon Library, CC BY 4.0",
        "expected_count": 500,
    },
    "scidraw": {
        "name": "SciDraw",
        "url": "https://scidraw.io",
        "license": "CC0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution_required": False,
        "expected_count": 700,
    },
    "servier": {
        "name": "Servier Medical Art",
        "url": "https://smart.servier.com",
        "license": "CC BY 3.0",
        "license_url": "https://creativecommons.org/licenses/by/3.0/",
        "attribution_required": True,
        "attribution": "Servier Medical Art (smart.servier.com), CC BY 3.0",
        "expected_count": 3000,
        "manual_download": True,
    },
}

# Coarse name/tag → category mapping. Per-source post-processing may refine this.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "cells/immune": ["t cell", "b cell", "macrophage", "dendritic", "neutrophil", "nk cell", "lymphocyte"],
    "cells/cancer": ["tumor", "cancer", "metastasis", "carcinoma"],
    "cells/blood": ["erythrocyte", "platelet", "rbc", "blood cell"],
    "cells/neuron": ["neuron", "axon", "dendrite", "synapse", "glia"],
    "cells/general": ["cell", "stem cell", "epithelial", "fibroblast"],
    "molecules/protein": ["protein", "antibody", "receptor", "enzyme", "kinase"],
    "molecules/nucleic_acid": ["dna", "rna", "mrna", "trna", "gene", "chromosome"],
    "molecules/small": ["molecule", "metabolite", "drug", "lipid", "amino acid"],
    "organelles": ["nucleus", "mitochondri", "ribosome", "endoplasmic", "golgi", "lysosome", "vesicle"],
    "tissues": ["epithelium", "muscle tissue", "connective"],
    "organs": ["heart", "liver", "lung", "brain", "kidney", "stomach", "pancreas", "intestine"],
    "equipment/lab": ["pipette", "tube", "flask", "centrifuge", "microscope", "incubator", "well plate", "petri"],
    "equipment/clinical": ["syringe", "stethoscope", "iv bag", "scalpel"],
    "pathways": ["signaling", "pathway", "cascade"],
    "arrows": ["arrow", "transition"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Filesystem-safe slug, capped at 80 chars."""
    s = re.sub(r"[^\w\-]+", "-", text.lower()).strip("-")
    return s[:80] if s else "unnamed"


def categorize(name: str, tags: list[str] | None) -> str:
    """Best-effort category lookup from name + tags."""
    haystack = " ".join([name] + (tags or [])).lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in haystack:
                return category
    return "uncategorized"


def make_record(
    source_id: str,
    item_id: str,
    name: str,
    tags: list[str] | None,
    file_relpath: str,
    license_override: str | None = None,
    attribution_override: str | None = None,
) -> dict:
    """Build a canonical index.json record for one element."""
    src = SOURCES_META[source_id]
    lic = license_override or src["license"]
    attr_req = src.get("attribution_required", False)
    if attr_req == "per_image":
        attr_req = bool(attribution_override)

    record: dict = {
        "id": f"{source_id}-{item_id}",
        "name": name,
        "tags": tags or [],
        "category": categorize(name, tags),
        "source": source_id,
        "source_name": src["name"],
        "source_url": src["url"],
        "license": lic,
        "license_url": src["license_url"],
        "attribution_required": bool(attr_req),
        "file": file_relpath,
        "added_at": str(date.today()),
    }
    if attr_req:
        record["attribution"] = attribution_override or src.get("attribution", "")
    return record


# ---------------------------------------------------------------------------
# Source 1: BioIcons (git clone)
# ---------------------------------------------------------------------------


def download_bioicons(target_root: Path) -> list[dict]:
    """Clone duerrsimon/bioicons and ingest every SVG it ships."""
    print("\n[1/6] Downloading BioIcons (CC0)...")
    src_meta = SOURCES_META["bioicons"]
    target_dir = target_root / "_raw" / "bioicons"
    target_dir.mkdir(parents=True, exist_ok=True)

    repo_dir = target_dir / "_repo"
    if repo_dir.exists():
        print(f"  [skip clone] {repo_dir} already exists, pulling...")
        subprocess.run(["git", "-C", str(repo_dir), "pull", "--quiet"], check=False)
    else:
        result = subprocess.run(
            ["git", "clone", "--depth=1", src_meta["repo"], str(repo_dir)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            print(f"  [ERROR] git clone failed: {result.stderr[:200]}", file=sys.stderr)
            return []

    # bioicons keeps its SVGs under icons/<category>/.
    icons_root = repo_dir / "icons"
    if not icons_root.exists():
        icons_root = repo_dir
    svg_files = list(icons_root.rglob("*.svg"))
    print(f"  Found {len(svg_files)} SVG files")

    records: list[dict] = []
    library_dir = target_root / "library"
    for svg in tqdm(svg_files, desc="  Importing", unit="icon"):
        try:
            relpath_in_repo = svg.relative_to(icons_root)
            category_hint = relpath_in_repo.parts[0] if len(relpath_in_repo.parts) > 1 else ""
            name = svg.stem.replace("_", " ").replace("-", " ")
            tags = [category_hint] if category_hint else []

            tmp = make_record("bioicons", slugify(svg.stem), name, tags, file_relpath="")
            target_subdir = library_dir / tmp["category"]
            target_subdir.mkdir(parents=True, exist_ok=True)

            target_path = target_subdir / f"bioicons-{slugify(svg.stem)}.svg"
            shutil.copy2(svg, target_path)

            tmp["file"] = str(target_path.relative_to(library_dir))
            records.append(tmp)
        except Exception as exc:
            tqdm.write(f"  [skip] {svg.name}: {exc}")

    print(f"  Imported {len(records)} icons")
    return records


# ---------------------------------------------------------------------------
# Source 2: PhyloPic v2 REST API
# ---------------------------------------------------------------------------
#
# Real-shape notes (verified against api.phylopic.org on 2026-05-03):
#   * Pagination shape:  GET /images?build=<N>&page=<i>&embed_items=true
#     - build       = API build number; api root advertises "?build=538" link.
#     - embed_items = REQUIRED, else `_embedded` is absent and items can't be enumerated.
#     - itemsPerPage is fixed by the server (currently 48). We honour totalPages.
#   * Per-item shape:
#     - item._links.vectorFile.href / sourceFile.href => ABSOLUTE URLs to SVG.
#     - item._links.specificNode.title              => human-readable taxon name.
#     - item._links.contributor.title               => contributor display name.
#     - item._links.license.href                    => CC URI; presence of
#       "publicdomain/zero" => CC0, else CC-BY (per PhyloPic policy).


def _phylopic_build_id(api_root: str) -> int:
    """Discover the current API build number by probing the root document."""
    try:
        resp = requests.get(f"{api_root}/", timeout=15)
        resp.raise_for_status()
        first_link = (resp.json().get("_links") or {}).get("self", {}).get("href", "")
        match = re.search(r"build=(\d+)", first_link)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    # Fallback: use the api images endpoint which 308s with build=N in headers.
    try:
        resp = requests.get(f"{api_root}/images", timeout=15)
        match = re.search(r"build=(\d+)", resp.url)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 0  # Many endpoints accept build=0 by 302-redirecting to current.


def download_phylopic(target_root: Path, max_count: int | None = None) -> list[dict]:
    """Page through PhyloPic v2 /images and ingest each silhouette."""
    print("\n[2/6] Downloading PhyloPic (CC0/CC-BY)...")
    src_meta = SOURCES_META["phylopic"]
    api = src_meta["api"]

    library_dir = target_root / "library"
    target_subdir = library_dir / "tissues" / "phylopic"
    target_subdir.mkdir(parents=True, exist_ok=True)

    build_id = _phylopic_build_id(api)
    if build_id == 0:
        print("  [warn] could not discover build id; PhyloPic may reject requests", file=sys.stderr)

    # Probe page 0 to learn totalPages.
    try:
        probe = requests.get(
            f"{api}/images",
            params={"build": build_id, "page": 0, "embed_items": "true"},
            timeout=20,
        )
        probe.raise_for_status()
        first = probe.json()
    except Exception as exc:
        print(f"  [skip] PhyloPic page 0 unreachable: {exc}", file=sys.stderr)
        return []

    total_pages = int(first.get("totalPages") or 1)
    total_items = int(first.get("totalItems") or 0)
    print(f"  PhyloPic reports build={build_id}, {total_items} items across {total_pages} pages")

    records: list[dict] = []
    seen_ids: set[str] = set()
    pbar = tqdm(desc="  Downloading", unit="image",
                total=min(total_items, max_count) if max_count else total_items)

    page = 0
    while page < total_pages:
        if max_count is not None and len(records) >= max_count:
            break
        try:
            if page == 0:
                payload = first  # already fetched
            else:
                resp = requests.get(
                    f"{api}/images",
                    params={"build": build_id, "page": page, "embed_items": "true"},
                    timeout=20,
                )
                if resp.status_code != 200:
                    tqdm.write(f"  [api error] page={page} status={resp.status_code}")
                    break
                payload = resp.json()
        except Exception as exc:
            tqdm.write(f"  [api error] page={page}: {exc}")
            break

        items = (payload.get("_embedded") or {}).get("items") or []
        if not items:
            break

        for item in items:
            uuid = item.get("uuid") or ""
            if not uuid or uuid in seen_ids:
                continue
            seen_ids.add(uuid)

            links = item.get("_links") or {}
            vf = links.get("vectorFile") or links.get("sourceFile") or {}
            href = vf.get("href")
            if not href:
                continue

            specific_node = links.get("specificNode") or {}
            name = specific_node.get("title") or item.get("attribution") or uuid[:8]
            contributor = (links.get("contributor") or {}).get("title", "unknown")

            license_uri = (links.get("license") or {}).get("href", "")
            is_cc0 = "publicdomain/zero" in license_uri or "publicdomain/mark" in license_uri
            license_short = "CC0" if is_cc0 else "CC-BY"
            attribution = "" if is_cc0 else f"PhyloPic image by {contributor} (CC BY)"

            try:
                # PhyloPic returns absolute URLs already; no urljoin needed.
                file_resp = requests.get(href, timeout=20)
                if file_resp.status_code != 200:
                    continue
                target_path = target_subdir / f"phylopic-{uuid[:8]}-{slugify(name)}.svg"
                target_path.write_bytes(file_resp.content)
            except Exception:
                continue

            rec = make_record(
                "phylopic", uuid[:8], name, ["silhouette"],
                file_relpath=str(target_path.relative_to(library_dir)),
                license_override=license_short,
                attribution_override=attribution,
            )
            records.append(rec)
            pbar.update(1)
            time.sleep(0.05)  # be polite

            if max_count is not None and len(records) >= max_count:
                break

        page += 1

    pbar.close()
    print(f"  Imported {len(records)} silhouettes")
    return records


# ---------------------------------------------------------------------------
# Source 3: NIH BioArt (manual ZIP drop — site is now a Next.js SPA)
# ---------------------------------------------------------------------------


def ingest_nih_bioart_zip(target_root: Path, zip_path: Path) -> list[dict]:
    """Ingest a user-supplied ZIP archive from bioart.niaid.nih.gov.

    The live site renders entirely client-side (Next.js) and exposes no public
    REST endpoint, so a HTTP scrape returns no illustration links. The
    expected workflow is: user opens https://bioart.niaid.nih.gov/, selects
    illustrations and clicks the per-illustration "Download" button (or the
    site's bulk-download where offered), zips the resulting folder, and points
    this script at the archive.
    """
    print("\n[3/6] Ingesting NIH BioArt ZIP (Public Domain)...")
    if not zip_path.exists():
        print(f"  [skip] {zip_path} 不存在 / not found", file=sys.stderr)
        print("  Workflow:", file=sys.stderr)
        print("    1) Browse https://bioart.niaid.nih.gov/ and download wanted illustrations.", file=sys.stderr)
        print("    2) Zip them into a single archive (any folder structure).", file=sys.stderr)
        print("    3) Re-run: --source nih_bioart --zip <path>", file=sys.stderr)
        return []

    library_dir = target_root / "library"
    target_subdir = library_dir / "uncategorized" / "nih_bioart"
    target_subdir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            wanted_exts = (".svg", ".ai", ".eps", ".png", ".jpg", ".jpeg")
            members = [n for n in zf.namelist() if n.lower().endswith(wanted_exts)]
            print(f"  Found {len(members)} usable files in {zip_path.name}")
            for entry in tqdm(members, desc="  Extracting", unit="file"):
                ext = entry.rsplit(".", 1)[-1].lower()
                stem = Path(entry).stem
                target_path = target_subdir / f"nihbioart-{slugify(stem)}.{ext}"
                with zf.open(entry) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
                rec = make_record(
                    "nih_bioart", slugify(stem), stem.replace("_", " "), [],
                    file_relpath=str(target_path.relative_to(library_dir)),
                )
                records.append(rec)
    except zipfile.BadZipFile as exc:
        print(f"  [error] {zip_path.name} is not a valid ZIP: {exc}", file=sys.stderr)
        return []

    print(f"  Imported {len(records)} files")
    return records


# ---------------------------------------------------------------------------
# Source 4: Reactome Icon Library (git clone)
# ---------------------------------------------------------------------------


def download_reactome(target_root: Path) -> list[dict]:
    """Clone reactome/icon-lib and ingest every SVG."""
    print("\n[4/6] Downloading Reactome Icon Library (CC BY 4.0)...")
    src_meta = SOURCES_META["reactome"]
    repo_dir = target_root / "_raw" / "reactome" / "_repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    if repo_dir.exists():
        subprocess.run(["git", "-C", str(repo_dir), "pull", "--quiet"], check=False)
    else:
        result = subprocess.run(
            ["git", "clone", "--depth=1", src_meta["repo"], str(repo_dir)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            print(f"  [skip] git clone failed: {result.stderr[:200]}", file=sys.stderr)
            return []

    svg_files = list(repo_dir.rglob("*.svg"))
    library_dir = target_root / "library"
    records: list[dict] = []
    for svg in tqdm(svg_files, desc="  Importing", unit="icon"):
        try:
            name = svg.stem.replace("_", " ")
            relpath_parts = svg.relative_to(repo_dir).parts
            category_hint = relpath_parts[0] if len(relpath_parts) > 1 else ""
            tags = [category_hint] if category_hint else []

            tmp = make_record("reactome", slugify(svg.stem), name, tags, file_relpath="")
            target_subdir = library_dir / tmp["category"]
            target_subdir.mkdir(parents=True, exist_ok=True)
            target_path = target_subdir / f"reactome-{slugify(svg.stem)}.svg"
            shutil.copy2(svg, target_path)
            tmp["file"] = str(target_path.relative_to(library_dir))
            records.append(tmp)
        except Exception as exc:
            tqdm.write(f"  [skip] {svg.name}: {exc}")

    print(f"  Imported {len(records)} icons")
    return records


# ---------------------------------------------------------------------------
# Source 5: SciDraw (webscrape — Janelia)
# ---------------------------------------------------------------------------


def download_scidraw(target_root: Path, max_count: int | None = None) -> list[dict]:
    """Best-effort scrape of scidraw.io drawing pages.

    SciDraw is a small static site, so listing-page scraping is reasonable.
    If the site changes structure, this function logs and returns [] without
    killing the rest of the pipeline.
    """
    print("\n[5/6] Downloading SciDraw (CC0)...")
    base = "https://scidraw.io"

    try:
        resp = requests.get(
            base, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 sci-pipeline/0.1"},
        )
        if resp.status_code != 200:
            print("  [skip] SciDraw unreachable", file=sys.stderr)
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        print(f"  [skip] {exc}", file=sys.stderr)
        return []

    library_dir = target_root / "library"
    target_subdir = library_dir / "cells" / "neuron" / "scidraw"
    target_subdir.mkdir(parents=True, exist_ok=True)

    drawings = soup.find_all("a", href=re.compile(r"/drawings/\d+"))
    seen: set[str] = set()
    records: list[dict] = []
    pbar = tqdm(drawings, desc="  Downloading", unit="image")
    try:
        for a in pbar:
            if max_count is not None and len(records) >= max_count:
                break
            url = f"{base}{a['href']}" if a["href"].startswith("/") else a["href"]
            if url in seen:
                continue
            seen.add(url)
            try:
                detail = requests.get(url, timeout=15)
                detail_soup = BeautifulSoup(detail.text, "html.parser")
                title_el = detail_soup.find("h1") or detail_soup.find("title")
                title = title_el.get_text(strip=True) if title_el else url.rsplit("/", 1)[-1]
                dl = detail_soup.find("a", href=re.compile(r"\.svg$", re.I))
                if not dl:
                    continue
                file_url = dl["href"]
                if file_url.startswith("/"):
                    file_url = base + file_url
                file_resp = requests.get(file_url, timeout=20)
                if file_resp.status_code != 200:
                    continue
                target_path = target_subdir / f"scidraw-{slugify(title)}.svg"
                target_path.write_bytes(file_resp.content)
                rec = make_record(
                    "scidraw", slugify(title), title, ["neuroscience"],
                    file_relpath=str(target_path.relative_to(library_dir)),
                )
                records.append(rec)
                time.sleep(0.15)
            except Exception:
                continue
    finally:
        pbar.close()
    print(f"  Imported {len(records)} drawings")
    return records


# ---------------------------------------------------------------------------
# Source 6: Servier Medical Art (extract from manually-downloaded PPTX)
# ---------------------------------------------------------------------------


def extract_servier_pptx(target_root: Path, pptx_path: Path) -> list[dict]:
    """Servier ships everything as a PowerPoint pack; ppt/media/ has the assets."""
    print("\n[6/6] Extracting Servier Medical Art from PPTX...")
    if not pptx_path.exists():
        print(f"  [skip] {pptx_path} 不存在 / not found", file=sys.stderr)
        print("  Workflow: download a PPTX pack from https://smart.servier.com,", file=sys.stderr)
        print("            then re-run: --source servier --pptx <path>", file=sys.stderr)
        return []

    library_dir = target_root / "library"
    target_subdir = library_dir / "_raw" / "servier"
    target_subdir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    try:
        with zipfile.ZipFile(pptx_path) as zf:
            media = [n for n in zf.namelist() if n.startswith("ppt/media/")]
            print(f"  Found {len(media)} media files in PPTX")
            for entry in tqdm(media, desc="  Extracting", unit="file"):
                ext = entry.rsplit(".", 1)[-1].lower()
                if ext not in ("emf", "wmf", "png", "svg", "jpg", "jpeg"):
                    continue
                stem = Path(entry).stem
                target_path = target_subdir / f"servier-{slugify(stem)}.{ext}"
                with zf.open(entry) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
                rec = make_record(
                    "servier", slugify(stem), stem.replace("_", " "), [],
                    file_relpath=str(target_path.relative_to(library_dir)),
                )
                records.append(rec)
    except zipfile.BadZipFile as exc:
        print(f"  [error] {pptx_path.name} is not a valid PPTX/ZIP: {exc}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"  [error] {exc}", file=sys.stderr)
        return []

    print(f"  Extracted {len(records)} files")
    print("  Note: EMF/WMF need LibreOffice or Inkscape conversion before they")
    print("        render in most non-Office tooling.")
    return records


# ---------------------------------------------------------------------------
# Index merge & summary
# ---------------------------------------------------------------------------


def merge_index(target_root: Path, new_records: list[dict]) -> tuple[int, int]:
    """Merge new records into library/index.json, dedupe by record id.

    Dedupes both against the on-disk index and within `new_records` itself —
    a single batch can produce duplicates (e.g. a source's API paginates the
    same item twice) and the downstream resolver assumes IDs are unique.
    """
    index_path = target_root / "library" / "index.json"
    if index_path.exists():
        try:
            existing: list[dict] = json.loads(index_path.read_text())
        except json.JSONDecodeError:
            print(f"  [warn] {index_path} is malformed; rewriting from scratch", file=sys.stderr)
            existing = []
    else:
        existing = []
    existing_ids = {r["id"] for r in existing}
    added = 0
    for r in new_records:
        rid = r["id"]
        if rid not in existing_ids:
            existing.append(r)
            existing_ids.add(rid)  # also dedupe within this batch
            added += 1
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    return added, len(existing)


def print_summary(target_root: Path) -> None:
    """Group + count library/index.json by source / category / license."""
    index_path = target_root / "library" / "index.json"
    if not index_path.exists():
        print(f"\n[summary] {index_path} not found — library is empty.")
        return
    try:
        records: list[dict] = json.loads(index_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"[summary] {index_path} malformed: {exc}", file=sys.stderr)
        return

    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_license: dict[str, int] = {}
    for r in records:
        by_source[r.get("source", "?")] = by_source.get(r.get("source", "?"), 0) + 1
        by_category[r.get("category", "?")] = by_category.get(r.get("category", "?"), 0) + 1
        by_license[r.get("license", "?")] = by_license.get(r.get("license", "?"), 0) + 1

    print(f"\n{'=' * 60}")
    print(f"Library overview  (root: {target_root})")
    print(f"{'=' * 60}")
    print(f"\nTotal: {len(records)}\n")
    print("By source:")
    for s, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {s:20s} {n:>6d}")
    print("\nBy category (top 12):")
    for c, n in sorted(by_category.items(), key=lambda x: -x[1])[:12]:
        print(f"  {c:25s} {n:>6d}")
    print("\nBy license:")
    for lic, n in sorted(by_license.items(), key=lambda x: -x[1]):
        print(f"  {lic:20s} {n:>6d}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download CC0/CC-BY scientific icons from 6 public sources",
    )
    parser.add_argument("--target", default="~/sci-illustration-library",
                        help="Local library root (default: ~/sci-illustration-library)")
    parser.add_argument("--source", choices=list(SOURCES_META.keys()),
                        help="Run a single source")
    parser.add_argument("--all", action="store_true", help="Run every non-manual source")
    parser.add_argument("--max-per-source", type=int, default=None,
                        help="Cap downloads per source (PhyloPic / SciDraw)")
    parser.add_argument("--pptx", type=str, default=None,
                        help="Servier PPTX path (only with --source servier)")
    parser.add_argument("--zip", type=str, default=None,
                        help="NIH BioArt ZIP path (only with --source nih_bioart)")
    parser.add_argument("--summary-only", action="store_true",
                        help="Print library stats without downloading")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    target_root = Path(args.target).expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "library").mkdir(exist_ok=True)

    if args.summary_only:
        print_summary(target_root)
        return 0

    if not args.all and not args.source:
        parser.error("--all or --source is required")

    # Network-touching paths require the third-party deps; fail early with
    # a single, actionable message instead of partway through a source.
    _require_runtime_deps()

    if args.all:
        # --all is documented as "every non-manual source"; quietly drop the
        # ones that need a user-supplied --pptx / --zip so the helpful
        # "requires --xxx" stub doesn't spam the overnight log.
        sources_to_run = [
            s for s, meta in SOURCES_META.items()
            if not meta.get("manual_download")
        ]
    else:
        sources_to_run = [args.source]

    for s in sources_to_run:
        try:
            new: list[dict]
            if s == "bioicons":
                new = download_bioicons(target_root)
            elif s == "phylopic":
                new = download_phylopic(target_root, args.max_per_source)
            elif s == "nih_bioart":
                if not args.zip:
                    print("\n[3/6] NIH BioArt requires --zip <path> (site is a SPA, no scrape).")
                    new = []
                else:
                    new = ingest_nih_bioart_zip(target_root, Path(args.zip).expanduser())
            elif s == "reactome":
                new = download_reactome(target_root)
            elif s == "scidraw":
                new = download_scidraw(target_root, args.max_per_source)
            elif s == "servier":
                if not args.pptx:
                    print("\n[6/6] Servier requires --pptx <path>; skipping.")
                    new = []
                else:
                    new = extract_servier_pptx(target_root, Path(args.pptx).expanduser())
            else:
                new = []
            added, total = merge_index(target_root, new)
            print(f"  → index.json: +{added} new, total {total}")
        except KeyboardInterrupt:
            print("\n[interrupted by user]", file=sys.stderr)
            break
        except Exception as exc:
            print(f"  [{s} failed] {exc}", file=sys.stderr)

    print_summary(target_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
