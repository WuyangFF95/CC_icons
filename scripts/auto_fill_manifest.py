#!/usr/bin/env python3
"""
auto_fill_manifest.py — generate a manifest YAML from a project description.

Today's flow: user reads the SVG template, lists every `data-placeholder`
id, then picks an element id for each via `library_tools.py search`.
26-placeholder templates like dark_proteome are tedious by hand.

This script automates step 1 + step 2:

  1. Parse the SVG template — extract every `data-placeholder` id and
     `<text id=>` id with positional context.
  2. Send the description + the placeholder list to a (provider-
     agnostic) LLM, asking it to propose a one-line semantics for
     each placeholder + a string for each label.
  3. For each proposed semantics, run `library_tools.py search` style
     matching against the unified index to get candidate ids; take
     the top hit (or surface ambiguity).
  4. Emit `manifest.yaml` with the picks.

Provider:
  - `--llm-mode mock` (default): no API key needed. Generates a
    placeholder-id-keyed manifest with empty values + a "TODO" comment
    block listing every placeholder so the user can fill in. Useful as a
    skeleton scaffolder while the live LLM call is wired up.
  - `--llm-mode live` + appropriate `*_API_KEY` env var: invokes the
    chosen provider via the official SDK if installed. Implementation
    deferred behind `NotImplementedError` until the user picks a
    canonical provider for this skill (likely MiniMax per SKILL.md
    §1.0a — same default as for whole-figure SVG writing).

Always preview with `--dry-run` first to see the LLM's proposals before
they hit disk.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from lxml import etree


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


# ---------------------------------------------------------------------------
# Template parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Placeholder:
    """A `<… data-placeholder="ID" x= y= width= height=>` slot."""
    placeholder_id: str
    tag: str
    x: float
    y: float
    width: float
    height: float

    @property
    def panel_letter(self) -> str | None:
        """Best-effort 'a/b/c/d' from `panel-X-…` ids."""
        m = re.match(r"panel-([a-z])-", self.placeholder_id)
        return m.group(1) if m else None


@dataclass(frozen=True)
class Label:
    """A `<text id="ID">` or `<tspan id="ID">` element waiting for content."""
    label_id: str
    x: float | None
    y: float | None
    placeholder_text: str  # what the template currently shows


def parse_template(template_path: Path) -> tuple[list[Placeholder], list[Label]]:
    """Walk the SVG once; return the lists of placeholders and labels."""
    tree = etree.parse(str(template_path))
    root = tree.getroot()

    placeholders: list[Placeholder] = []
    for elem in root.xpath('.//svg:*[@data-placeholder]', namespaces=SVG_NS):
        try:
            ph = Placeholder(
                placeholder_id=elem.get("data-placeholder"),
                tag=etree.QName(elem.tag).localname,
                x=float(elem.get("x") or 0.0),
                y=float(elem.get("y") or 0.0),
                width=float(elem.get("width") or 0.0),
                height=float(elem.get("height") or 0.0),
            )
        except (ValueError, TypeError):
            continue
        placeholders.append(ph)

    labels: list[Label] = []
    for tag in ("svg:text", "svg:tspan"):
        for elem in root.xpath(f'.//{tag}[@id]', namespaces=SVG_NS):
            text = (elem.text or "").strip()
            if not text:
                # skip unlabeled <tspan> wrappers
                continue
            try:
                x_raw = elem.get("x"); y_raw = elem.get("y")
                x_v = float(x_raw) if x_raw else None
                y_v = float(y_raw) if y_raw else None
            except ValueError:
                x_v = y_v = None
            labels.append(Label(
                label_id=elem.get("id"),
                x=x_v, y=y_v,
                placeholder_text=text[:120],
            ))
    return placeholders, labels


# ---------------------------------------------------------------------------
# Library lookup
# ---------------------------------------------------------------------------


def load_library_records(library_root: Path) -> list[dict]:
    """Same minimal index walk as library_tools.load_unified_index."""
    records: list[dict] = []
    json_idx = library_root / "library" / "index.json"
    if json_idx.exists():
        try:
            data = json.loads(json_idx.read_text())
            records.extend(r for r in data if r.get("id"))
        except (json.JSONDecodeError, OSError):
            pass
    # We skip legacy YAML for simplicity — the JSON bulk index covers the
    # bootstrap-fed CC0 library, which is the realistic input here.
    return records


def search_library(records: list[dict], semantics: str) -> str | None:
    """Return the best-matching record id, or None."""
    if not semantics:
        return None
    query = semantics.lower().strip()
    # Score by # of query tokens that hit name + category + tags.
    tokens = [t for t in re.split(r"[\s\-_/]+", query) if len(t) > 2]
    best: tuple[int, dict] | None = None
    for r in records:
        haystack = " ".join([
            (r.get("name") or "").lower(),
            (r.get("category") or "").lower(),
            " ".join(r.get("tags", [])).lower(),
            (r.get("id") or "").lower(),
        ])
        score = sum(1 for t in tokens if t in haystack)
        if score == 0:
            continue
        if best is None or score > best[0]:
            best = (score, r)
    return best[1]["id"] if best else None


# ---------------------------------------------------------------------------
# Semantics propose: mock + live
# ---------------------------------------------------------------------------


def propose_semantics_mock(
    description: str,
    placeholders: list[Placeholder],
    labels: list[Label],
) -> tuple[dict[str, str], dict[str, str]]:
    """Mock provider: scaffold a manifest skeleton for the user to fill in.

    For each placeholder it derives a TODO marker that includes the panel
    letter and a hint extracted from the description's first sentence; for
    each label it leaves the original placeholder text as a starting point.
    """
    first_sentence = re.split(r"[.。!?\n]", description.strip(), maxsplit=1)[0]
    hint = first_sentence[:80] if first_sentence else "describe element"

    panels: dict[str, str] = {}
    for ph in placeholders:
        panel_hint = f"[panel {ph.panel_letter}] " if ph.panel_letter else ""
        panels[ph.placeholder_id] = f"# TODO: {panel_hint}{hint}"

    labels_out: dict[str, str] = {}
    for lb in labels:
        labels_out[lb.label_id] = lb.placeholder_text  # keep template default
    return panels, labels_out


def propose_semantics_live(
    description: str,
    placeholders: list[Placeholder],
    labels: list[Label],
) -> tuple[dict[str, str], dict[str, str]]:
    """Live LLM call. Deferred — see issue #3 / #9 for the wiring TODO."""
    raise NotImplementedError(
        "Live LLM call not yet wired up. Use --llm-mode mock for the scaffold "
        "today; the live mode will land once we pick a canonical provider for "
        "this skill (likely MiniMax per SKILL.md §1.0a)."
    )


# ---------------------------------------------------------------------------
# Manifest writer
# ---------------------------------------------------------------------------


def build_manifest(
    library_root: Path,
    placeholders: list[Placeholder],
    labels: list[Label],
    semantics_panels: dict[str, str],
    semantics_labels: dict[str, str],
    records: list[dict],
) -> dict:
    """Assemble the final manifest dict, resolving each semantics to an element id."""
    panels_resolved: dict[str, str] = {}
    todo_notes: dict[str, str] = {}

    for ph in placeholders:
        proposed = semantics_panels.get(ph.placeholder_id, "")
        if proposed.startswith("# TODO"):
            todo_notes[ph.placeholder_id] = proposed
            panels_resolved[ph.placeholder_id] = ""
            continue
        match = search_library(records, proposed)
        if match:
            panels_resolved[ph.placeholder_id] = match
        else:
            todo_notes[ph.placeholder_id] = (
                f"# UNRESOLVED: '{proposed}' — no library hit"
            )
            panels_resolved[ph.placeholder_id] = ""

    return {
        "library_root": str(library_root),
        "_auto_fill_notes": todo_notes,
        "panels": panels_resolved,
        "labels": semantics_labels,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a manifest YAML from a SVG template + project description.",
    )
    parser.add_argument("--template", type=Path, required=True,
                        help="SVG template with data-placeholder + text id slots")
    parser.add_argument("--description", type=str, default=None,
                        help="one-paragraph project description (read from --description-file "
                             "if omitted)")
    parser.add_argument("--description-file", type=Path, default=None,
                        help="alternative to --description: read from a text file")
    parser.add_argument("--output", type=Path, required=True,
                        help="manifest YAML output path")
    parser.add_argument("--library-root", type=Path,
                        default=Path.home() / "sci-illustration-library",
                        help="library root containing library/index.json")
    parser.add_argument("--llm-mode", choices=["mock", "live"], default="mock",
                        help="`mock` (default): scaffold a skeleton manifest with TODO notes; "
                             "`live`: call the configured LLM (not yet wired — see #9)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the manifest to stdout instead of writing the file")
    args = parser.parse_args()

    # Resolve description.
    if args.description:
        description = args.description
    elif args.description_file:
        if not args.description_file.exists():
            print(f"Error: description file not found: {args.description_file}", file=sys.stderr)
            return 1
        description = args.description_file.read_text()
    else:
        print("Error: pass --description or --description-file.", file=sys.stderr)
        return 1

    if not args.template.exists():
        print(f"Error: template not found: {args.template}", file=sys.stderr)
        return 1

    placeholders, labels = parse_template(args.template)
    print(f"Template: {args.template.name}")
    print(f"  placeholders: {len(placeholders)}")
    print(f"  labels:       {len(labels)}")

    library_root = args.library_root.expanduser().resolve()
    records = load_library_records(library_root)
    print(f"Library:  {len(records)} records under {library_root}")

    if args.llm_mode == "live":
        try:
            sem_panels, sem_labels = propose_semantics_live(description, placeholders, labels)
        except NotImplementedError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        sem_panels, sem_labels = propose_semantics_mock(description, placeholders, labels)
        print("\n[mock] generated TODO scaffold; replace each `# TODO` panel value with a "
              "real semantics line then re-run with --llm-mode live to auto-resolve.")

    manifest = build_manifest(library_root, placeholders, labels,
                              sem_panels, sem_labels, records)

    # Coverage report.
    panel_total = len(manifest["panels"])
    panel_resolved = sum(1 for v in manifest["panels"].values() if v)
    print(f"\nResolved {panel_resolved} / {panel_total} placeholders to library ids "
          f"({(panel_resolved * 100) // max(panel_total, 1)}%).")
    if panel_resolved < panel_total:
        unresolved = panel_total - panel_resolved
        print(f"  {unresolved} placeholder(s) still need attention — see "
              f"`_auto_fill_notes` in the output manifest.")

    yaml_text = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)

    if args.dry_run:
        print("\n--- dry-run: would write manifest below ---\n")
        print(yaml_text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml_text, encoding="utf-8")
        print(f"\nWrote: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
