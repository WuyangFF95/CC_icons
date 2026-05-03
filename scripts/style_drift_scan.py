#!/usr/bin/env python3
"""
style_drift_scan.py — periodic style-reference drift check.

Recraft V3 element generation locks visual style via a `style_id` that is
nominally stable but can drift if Recraft re-trains the underlying style.
Without periodic verification, the library silently slides away from the
canonical exemplars under `_style-references/`.

This script:
  1. Loads every `style_id` actually used in the unified library index.
  2. For each style_id, runs a known prompt against Recraft to get a
     fresh sample (or, in `--dry-run` / `--mock-provider`, fakes one).
  3. Compares the new sample to the canonical
     `_style-references/<style>.svg` via a perceptual-hash distance
     (8×8 average-hash, Hamming distance, 0–64).
  4. Writes a markdown drift report grouped by style_id; flags any
     style whose drift is above `--threshold-bits` (default 12 bits).

The scan is designed to run weekly via `/schedule`. In CI / dry-run
mode, no API calls are made and the report comes back saying "0
drift" — useful as a smoke check that the pipeline is wired correctly
before exposing the live API key.

Usage:
  python scripts/style_drift_scan.py --dry-run
  python scripts/style_drift_scan.py \
      --library-root ~/sci-illustration-library \
      --report drift-report.md
  python scripts/style_drift_scan.py --threshold-bits 8 --auto-update-references
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

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


DEFAULT_LIBRARY_ROOT = Path.home() / "sci-illustration-library"

# A neutral prompt used to probe each style_id. Should be stable across runs
# so that hash differences attribute to the *style*, not the *content*.
PROBE_PROMPT = "a single flat-style icon of a hepatocyte, transparent background"


# ---------------------------------------------------------------------------
# pHash (8×8 average-hash) — 64-bit fingerprint
# ---------------------------------------------------------------------------


def _png_to_8x8_grayscale(data: bytes) -> bytes | None:
    """Resize the PNG bytes to 8×8 grayscale; return raw 64-byte buffer."""
    if not HAS_PILLOW:
        return None
    from io import BytesIO
    try:
        img = Image.open(BytesIO(data)).convert("L").resize((8, 8), Image.LANCZOS)
        return img.tobytes()
    except (OSError, ValueError):
        return None


def average_hash(buf: bytes) -> int:
    """Classic average-hash: 1 if pixel ≥ mean, else 0. 64 bits packed into an int."""
    if not buf or len(buf) != 64:
        return 0
    mean = sum(buf) / 64
    bits = 0
    for i, px in enumerate(buf):
        if px >= mean:
            bits |= 1 << (63 - i)
    return bits


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def hash_svg(svg_path: Path) -> int | None:
    """Render SVG → 8×8 grayscale → average-hash. None on failure."""
    if not HAS_CAIROSVG or not HAS_PILLOW:
        return None
    try:
        png = cairosvg.svg2png(url=str(svg_path), output_width=64, output_height=64)
    except (OSError, ValueError):
        return None
    buf = _png_to_8x8_grayscale(png)
    return average_hash(buf) if buf else None


# ---------------------------------------------------------------------------
# Provider abstraction (Recraft live vs mock)
# ---------------------------------------------------------------------------


@dataclass
class _MockProvider:
    """A no-op provider that 'regenerates' by returning the existing reference.

    Distance is 0; useful for `--dry-run` and as a wiring smoke test.
    """
    references_dir: Path

    def regenerate(self, style_id: str, prompt: str) -> Path | None:
        candidate = self.references_dir / f"{style_id}.svg"
        return candidate if candidate.exists() else None


@dataclass
class _RecraftProvider:
    """Live Recraft v3 provider. Implementation deferred until BioRender-MCP
    pattern is finalized — see issue #3 / hint in README. For now this raises
    if invoked, so the user has to pass `--mock-provider` explicitly until
    the live wiring lands. The wiring is intentionally a separate ticket so
    the drift-scan plumbing can ship and be tested today.
    """
    api_key: str
    references_dir: Path

    def regenerate(self, style_id: str, prompt: str) -> Path | None:
        raise NotImplementedError(
            "Live Recraft regeneration is not yet wired up. Pass "
            "`--mock-provider` to use the in-place exemplar as the 'fresh' "
            "sample (smoke-test only), or implement the call inside "
            "_RecraftProvider.regenerate() and remove this exception."
        )


# ---------------------------------------------------------------------------
# Drift scan
# ---------------------------------------------------------------------------


@dataclass
class StyleResult:
    style_id: str
    reference_path: Path
    sample_path: Path | None = None
    distance_bits: int | None = None
    error: str | None = None


@dataclass
class ScanReport:
    started: str
    threshold_bits: int
    results: list[StyleResult] = field(default_factory=list)

    def by_severity(self) -> tuple[list[StyleResult], list[StyleResult], list[StyleResult]]:
        """Return (drifted, ok, errored) buckets."""
        drifted: list[StyleResult] = []
        ok: list[StyleResult] = []
        errored: list[StyleResult] = []
        for r in self.results:
            if r.error is not None:
                errored.append(r)
            elif r.distance_bits is not None and r.distance_bits >= self.threshold_bits:
                drifted.append(r)
            else:
                ok.append(r)
        return drifted, ok, errored


def _collect_style_ids(library_root: Path) -> list[str]:
    """Return distinct `style_id` values registered in the library."""
    style_ids: set[str] = set()
    # CC0 bulk index might carry a `style_id` field if a future register
    # tier writes it; tolerate missing.
    json_index = library_root / "library" / "index.json"
    if json_index.exists():
        try:
            for r in json.loads(json_index.read_text()):
                sid = r.get("style_id") or (r.get("_meta") or {}).get("style_id")
                if sid:
                    style_ids.add(sid)
        except (json.JSONDecodeError, OSError):
            pass
    # Legacy per-category YAMLs definitely have it.
    for category in ("cells", "molecules", "organelles", "tissues",
                     "organs", "equipment", "pathways", "arrows"):
        yp = library_root / category / "_index.yaml"
        if not yp.exists():
            continue
        try:
            import yaml
            data = yaml.safe_load(yp.read_text()) or {}
            for entry in data.get("elements", []):
                sid = entry.get("style_id")
                if sid:
                    style_ids.add(sid)
        except Exception:  # noqa: BLE001
            continue
    return sorted(style_ids)


def scan(
    library_root: Path,
    *,
    threshold_bits: int,
    regenerate: Callable[[str, str], Path | None],
    references_dir: Path,
) -> ScanReport:
    report = ScanReport(started=date.today().isoformat(), threshold_bits=threshold_bits)
    style_ids = _collect_style_ids(library_root)
    if not style_ids:
        return report

    for sid in style_ids:
        ref_path = references_dir / f"{sid}.svg"
        result = StyleResult(style_id=sid, reference_path=ref_path)

        if not ref_path.exists():
            result.error = f"reference SVG not found: {ref_path}"
            report.results.append(result)
            continue

        try:
            sample_path = regenerate(sid, PROBE_PROMPT)
        except NotImplementedError as exc:
            result.error = f"provider error: {exc}"
            report.results.append(result)
            continue

        if sample_path is None or not sample_path.exists():
            result.error = "regenerate() returned no sample"
            report.results.append(result)
            continue

        result.sample_path = sample_path
        ref_hash = hash_svg(ref_path)
        sample_hash = hash_svg(sample_path)
        if ref_hash is None or sample_hash is None:
            result.error = "could not hash reference and/or sample (cairosvg/Pillow missing?)"
            report.results.append(result)
            continue

        result.distance_bits = hamming_distance(ref_hash, sample_hash)
        report.results.append(result)
    return report


def render_markdown(report: ScanReport) -> str:
    drifted, ok, errored = report.by_severity()
    lines: list[str] = [
        "# Style drift scan",
        f"_Generated {report.started} · threshold = {report.threshold_bits} bits / 64_",
        "",
        f"- Drifted (≥ threshold): **{len(drifted)}**",
        f"- OK:                    {len(ok)}",
        f"- Errored:               {len(errored)}",
        "",
    ]
    if drifted:
        lines.append("## Drift over threshold")
        lines.append("")
        for r in drifted:
            lines.append(f"- `{r.style_id}` — distance **{r.distance_bits}** / 64")
        lines.append("")
    if ok:
        lines.append("## Stable")
        lines.append("")
        for r in ok:
            lines.append(f"- `{r.style_id}` — distance {r.distance_bits} / 64")
        lines.append("")
    if errored:
        lines.append("## Errored (no judgement issued)")
        lines.append("")
        for r in errored:
            lines.append(f"- `{r.style_id}` — {r.error}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect Recraft style drift against the canonical exemplars "
                    "in `_style-references/`.",
    )
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT,
                        help=f"library root (default: {DEFAULT_LIBRARY_ROOT})")
    parser.add_argument("--references-dir", type=Path, default=None,
                        help="dir containing <style_id>.svg exemplars "
                             "(default: <library-root>/_style-references)")
    parser.add_argument("--threshold-bits", type=int, default=12,
                        help="Hamming distance (out of 64) above which to flag "
                             "drift (default: 12 bits ≈ 18.75%)")
    parser.add_argument("--report", type=Path, default=None,
                        help="markdown report output path (default: stdout)")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't call Recraft; treat the existing exemplar "
                             "as the regenerated sample (distance always 0). "
                             "Use this to verify wiring without burning API credit.")
    parser.add_argument("--mock-provider", action="store_true",
                        help="alias for --dry-run; explicit when a CI uses no API key")
    parser.add_argument("--auto-update-references", action="store_true",
                        help="(future) replace `_style-references/<id>.svg` with the "
                             "regenerated sample when distance < threshold; bumps the "
                             "exemplar to track 'real' Recraft updates. NOOP today "
                             "since the live provider isn't wired.")
    args = parser.parse_args()

    library_root = args.library_root.expanduser().resolve()
    references_dir = (
        args.references_dir.expanduser().resolve()
        if args.references_dir
        else library_root / "_style-references"
    )

    if not references_dir.is_dir():
        print(f"Error: references dir not found: {references_dir}", file=sys.stderr)
        print("Create it and add <style_id>.svg files first.", file=sys.stderr)
        return 1

    if args.dry_run or args.mock_provider:
        provider = _MockProvider(references_dir=references_dir)
    else:
        api_key = os.environ.get("RECRAFT_API_KEY")
        if not api_key:
            print("Error: RECRAFT_API_KEY not set; pass --dry-run or --mock-provider "
                  "to skip the live call.", file=sys.stderr)
            return 1
        provider = _RecraftProvider(api_key=api_key, references_dir=references_dir)

    report = scan(
        library_root,
        threshold_bits=args.threshold_bits,
        regenerate=provider.regenerate,
        references_dir=references_dir,
    )
    md = render_markdown(report)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(md, encoding="utf-8")
        print(f"Report: {args.report}")
    else:
        print(md)

    drifted, _, errored = report.by_severity()
    if drifted:
        return 1  # gate-suitable
    if errored and not (args.dry_run or args.mock_provider):
        return 1  # only treat errors as failure when running for real
    return 0


if __name__ == "__main__":
    sys.exit(main())
