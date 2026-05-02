# CC_icons — `scientific-illustration-asset-pipeline`

Local home for the `scientific-illustration-asset-pipeline` skill that turns
ad-hoc scientific figure work into a reusable, license-clean element library
plus a half-automated assembly pipeline.

The skill itself ships in [`SKILL.md`](./SKILL.md). This README is the
operator-facing quickstart for the bootstrap and assembly tooling that lives
in [`scripts/`](./scripts) and [`templates/`](./templates).

> Skill version: see frontmatter in [`SKILL.md`](./SKILL.md). Open changes
> are tracked under [`openspec/changes/`](./openspec/changes/).

---

## Bootstrap the CC0 element library / 构建 CC0 元件库

> One-time setup. Plan for **~one overnight run** and **~500 MB on disk**
> for the full sweep; PhyloPic is the dominant cost.
>
> 一次性安装。完整跑完约**通宵一晚**，磁盘**~500 MB**；PhyloPic 是最大头。

### 1. Install dependencies / 安装依赖

```bash
pip install requests beautifulsoup4 lxml tqdm pyyaml \
            cairosvg pillow python-pptx
```

| Package | Used by | Mandatory? |
|---|---|---|
| `requests` / `beautifulsoup4` / `lxml` / `tqdm` / `pyyaml` | `download_cc0_seed.py`, `library_tools.py`, `assemble_figure.py` | **Yes** |
| `cairosvg` + `pillow` | `library_tools.py preview` / `export` | only for thumbnail / PPTX export |
| `python-pptx` | `library_tools.py export`, `assemble_figure.py --export-pptx` | only when exporting to PPTX |
| Inkscape ≥ 1.2 (CLI) | `assemble_figure.py --export-pdf*` | only when exporting to PDF |

### 2. Run the full bootstrap / 整库 bootstrap

```bash
python scripts/download_cc0_seed.py --all --target ~/sci-illustration-library
```

This walks every source that does **not** require a manual file:

- **BioIcons** — git clone (CC0)
- **PhyloPic v2** — REST API (CC0/CC-BY per image)
- **Reactome Icon Library** — git clone (CC BY 4.0)
- **SciDraw** — light scrape of scidraw.io (CC0)

### 3. Add the manual sources / 加上需要手动下载的两源

Two sources cannot be auto-fetched (the live sites are SPAs or gate downloads
behind a click-through). Drop the archives anywhere local and point the
script at them:

```bash
# Servier Medical Art — get the official PPTX from https://smart.servier.com
python scripts/download_cc0_seed.py --source servier \
       --pptx ~/Downloads/servier-anatomy.pptx

# NIH BioArt (NIAID) — pick illustrations from https://bioart.niaid.nih.gov/
# and zip the resulting downloads
python scripts/download_cc0_seed.py --source nih_bioart \
       --zip ~/Downloads/nih_bioart_pack.zip
```

### 4. Inspect the library / 浏览成果

```bash
# Stats overview
python scripts/library_tools.py stats

# Search and filter
python scripts/library_tools.py search "alpha helix" --license CC0
python scripts/library_tools.py search --category cells/immune --max 20

# Thumbnail grid PNG
python scripts/library_tools.py preview --query "neuron" --output preview.png

# Export selected elements to PPTX (one slide per element)
python scripts/library_tools.py export \
       --ids "bioicons-tcell-activated,phylopic-fd8d3e5e" \
       --output ./elements.pptx

# Generate a CC-BY attribution clip ready to paste into a figure caption
python scripts/library_tools.py attribution --output ./fig1-attribution.md
```

### 5. Assemble a figure / 组装一张 figure

```bash
python scripts/assemble_figure.py \
       --template templates/dark_proteome_4panel_template.svg \
       --manifest templates/dark_proteome_manifest.yaml \
       --output ./fig1.svg \
       --export-pdf --export-pptx
```

The assembler:

1. resolves each `panels:` value as either a unified-index ID
   (`bioicons-tcell-activated`) or a relative path (`cells/treg-flat-v1.svg`),
2. fills `<text id="…">` slots from the manifest's `labels:` map,
3. injects an automatic `Adapted from: …` caption at the bottom-right when
   any CC-BY element was used,
4. normalises fonts and exports SVG / PDF / PPTX as requested.

---

## Out-of-scope (planned for v0.2)

- BioRender connector (waiting on stable MCP exposure).
- `text_collision_check.py` — auto-detect `<text>` bbox overlaps in
  LLM-generated SVG.
- A web UI for library browsing.
- Per-journal config (Nature / Cell / Lancet color/font specs).
- **Low-fidelity tracing layer**: re-trace every CC0 / AI element through
  Inkscape (or Adobe Illustrator) before assembly so visually heterogeneous
  sources converge to one stylebook, then nest into Inkscape / PPT for
  PDF / SVG-with-nested-SVG output. Independent step, orthogonal to v0.1.1's
  ID-resolver and attribution work.

---

## License

Code in this repository is MIT-licensed (see [`LICENSE`](./LICENSE)). Element
content downloaded by the bootstrap inherits **the original source's** license;
`library/index.json` records the exact CC URI per element so that
`library_tools.py attribution` can cite each source correctly. Always re-check
attribution requirements before publishing — the script is best effort, not
legal advice.
