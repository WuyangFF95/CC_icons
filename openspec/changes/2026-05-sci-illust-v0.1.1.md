---
change_id: sci-illust-v0.1.1
title: scientific-illustration-asset-pipeline v0.1.0 → v0.1.1
status: proposed
authors: [WuyangFF95, claude-code (sonnet/opus)]
created: 2026-05-03
target_branch: feat/sci-illust-pipeline-v0.1.1
---

# Sci Illustration Pipeline — v0.1.0 → v0.1.1
# 科研插图流水线 — v0.1.0 → v0.1.1

## 1. Intent · 意图

Repath the pipeline based on a real-world failure mode discovered in v0.1.0: **Recraft V3 cannot serve as the whole-figure provider** for scientific figures because diffusion-based output rasterizes text into 0 `<text>` nodes + 2308 `<path>` nodes (1.8 MB SVG). Switch the whole-figure track to **LLMs writing SVG code directly** (Claude / MiniMax / GLM-5.1 / GPT-5), and bootstrap a **CC0 element library** (~17 000 elements from 6 public sources) so element-level reinforcement no longer burns Recraft credit.

基于 v0.1.0 实战暴露的失败模式做路径修正：扩散模型不可能产文字真节点的 SVG，整图主力改 LLM 直出 SVG 代码；同时 bootstrap 一个 CC0 元件库（6 源 ~17000 元件），元件层不再烧 Recraft credit。

## 2. Scope · 范围

### In scope · 在范围内

| File · 文件 | Change · 变更 |
|---|---|
| `SKILL.md` | bump `0.1.0 → 0.1.1`; insert v0.1.1 change-summary block; add chapter "零 / CC0 Bootstrap"; add §1.0 (whole-figure vs element track) and §1.0a (LLM-writes-SVG prompt template); revise §4 Level 3 verdict; update §9 known limits & v0.2 plan |
| `scripts/download_cc0_seed.py` (new, ~700 lines) | 6 sources: BioIcons / PhyloPic / NIH BioArt / Reactome / SciDraw / Servier — git clone, REST API, manual-download modes |
| `scripts/library_tools.py` (new, ~775 lines) | preserves conceptual ops (qc / register / search / audit / check) + adds 4 new commands (stats / preview / export / attribution); unified-index loader merging `library/index.json` (CC0 bulk) and per-category `_index.yaml` (legacy) |
| `scripts/assemble_figure.py` (new, ~340 lines) | template-based fill with unified-ID resolution + auto attribution injection + PNG/EMF fallback |
| `templates/dark_proteome_4panel_template.svg` (new) | 1600×1000 4-panel template, color-coded panels, central theme circle, legend strip, all placeholders & text-IDs |
| `templates/dark_proteome_manifest.yaml` (new) | companion YAML mapping placeholders → IDs/paths and text-IDs → strings |
| `README.md` (new, bootstrap section) | install deps + run command + expected runtime/disk + Servier manual step |

### Out of scope · 不在范围内（v0.2 candidates）

- **BioRender connector integration** — wait for MCP tools to surface
- **`text_collision_check.py`** — auto-detect `<text>` bbox overlaps in LLM-generated SVG
- **Web UI** for library browsing (current: CLI only)
- **Per-journal config** files (Nature / Cell / Lancet color/font specs)
- **LLM-driven `auto_fill_manifest.py`** — auto-populate manifest from project description
- **(NEW, from user 2026-05-03)** **Low-fidelity tracing layer**: pre-process every CC0/AI element through Inkscape/Illustrator low-fi trace before assembly so visually heterogeneous sources converge to one stylebook, then nest into Inkscape/PPT for PDF/SVG-with-nested-SVG output. Independent step, orthogonal to v0.1.1's ID-resolver and attribution work.

## 3. Hard Decisions · 不可重议的决策

Inherited from the design session that produced the v0.1.1 zip; do **not** re-litigate:

1. **No BioRender batch integration** in this PR (user gave MCP docs but explicitly queued separately).
2. **No paid-SaaS asset scraping**. CC0/CC-BY public sources cover ~80% of element needs.
3. **No removal of v0.1.0 functionality**. Recraft / Gemini / Zhipu / MiniMax stay; Recraft demoted from "whole figure" to "semi-3D element reinforcement".
4. **Bilingual commit / PR / reply**. Hard user preference.
5. **OpenSpec first → 5 commits, not one mega-commit.**

## 4. Audit findings · 实测核对发现（修正项）

The v0.1.1 design zip was assembled in a separate Claude.ai session. This implementation session ran external API smoke tests and found the following discrepancies that **must** be patched in the same PR:

### 4.1 PhyloPic v2 API (`download_cc0_seed.py`)
| Field · 字段 | Zip wrote · zip 写法 | Actual · 实际 |
|---|---|---|
| Required query param | `embed_specificNode=false&embed_contributor=true` | **must include `embed_items=true`** else `_embedded` absent |
| Page size | `size=50` (request) | server-fixed `itemsPerPage=48`, ignored |
| Vector URL location | `_links.vectorFile.href` (relative, urljoin needed) | **absolute URL** under `_links.vectorFile.href` or `_links.sourceFile.href` |
| Item name | `_embedded.specificNode.names[0].string` | **`_links.specificNode.title`** |
| Pagination loop | hit-empty stop | use `totalPages` field |

Fix shape: rewrite `download_phylopic` to honor real API.

### 4.2 NIH BioArt site (`download_cc0_seed.py`)
- `bioart.niaid.nih.gov` is now a Next.js SPA. `/api/*`, `/sitemap.xml`, server-rendered cards — **all 404**. `BeautifulSoup` finds zero illustration links.
- **Action**: change `download_nih_bioart` to `manual-download` mode (same pattern as Servier). User downloads ZIP/folder from the site UI, script ingests it. SKILL.md notes the SPA reality.

### 4.3 Library tools (`library_tools.py`)
- `Emu` import — unused, drop.
- `from io import BytesIO` deep inside loop — hoist to module top.
- `tmp_dir = Path("/tmp")` — non-portable; switch to `tempfile.gettempdir()` or `tempfile.mkdtemp()`.
- `for (source, license), items in ...` shadows module-level usage of `r["license"]`. Rename to `lic`.
- `except Exception as e:` then `e` unused (line 514) — make explicit.
- `font.color.rgb = None` — no-op or raises; drop the line.

### 4.4 Template (`dark_proteome_4panel_template.svg`)
- Manifest references label `fig-title` but the SVG only has `<title id="fig-title">` (used by accessibility, not by `xpath('//svg:text[@id=...]')` in `assemble_figure.py`). **Add a real `<text id="fig-title">` element** at canvas top so the placeholder resolves; keep `<title>` for a11y.

### 4.5 `download_cc0_seed.py` housekeeping
- Drop dead `file_hash()` (unused).
- Drop `python-pptx` from declared deps; the Servier extractor uses stdlib `zipfile`.

## 5. Design Decisions · 设计决策

### D1. Why a unified `library/index.json` + legacy `_index.yaml`
JSON for bulk-imported CC0 (single file, fast load, easy diff), YAML per-category for human-edited legacy registrations (preserves comments, additive). `load_unified_index()` merges both; legacy records get `_legacy: true`.

### D2. Why placeholders are `<rect data-placeholder="ID">` not `<use>`
A `<rect>` survives `xpath` queries when the manifest skips the ID (rendered as a faint dashed outline showing what slot was left empty). `<use href>` would render nothing. Placeholders carry `x/y/width/height` so PNG/EMF fallback can reuse the geometry.

### D3. Why ID-vs-path dual resolution in `assemble_figure.py`
Lets the manifest mix CC0 IDs (`bioicons-tcell-activated`) with hand-managed paths (`manual-charts/protein-length-hist.svg`). Disambiguation: `"/" in value or value endswith {.svg/.png/.emf}` → file path; else → ID lookup in unified index.

### D4. Why attribution is auto-injected in `assemble_figure.py` and not deferred
Investigators forget. CC-BY violation in published figure ≫ 8px gray bottom-right caption.

## 6. Validation Strategy · 验证策略

Pre-push checklist (no network):

```bash
# Python syntax
python -c "import ast; [ast.parse(open(f).read()) for f in [
  'scripts/download_cc0_seed.py',
  'scripts/library_tools.py',
  'scripts/assemble_figure.py'
]]"

# CLI help (every subcommand)
python scripts/library_tools.py --help
for cmd in qc register search audit check stats preview export attribution; do
  python scripts/library_tools.py $cmd --help > /dev/null && echo "[OK] $cmd"
done

# Template + manifest validity
python -c "from lxml import etree; etree.parse('templates/dark_proteome_4panel_template.svg')"
python -c "import yaml; yaml.safe_load(open('templates/dark_proteome_manifest.yaml'))"

# download_cc0_seed.py summary mode (no network)
python scripts/download_cc0_seed.py --summary-only --target /tmp/empty-test-lib

# SKILL.md frontmatter sanity
grep '^version: 0.1.1' SKILL.md

# Section count
grep -c '^## ' SKILL.md  # ≥ 11 expected
```

Network-dependent paths (PhyloPic, BioIcons clone, Reactome clone) are **not exercised in CI**; they're documented in README.md and validated only on the user's overnight bootstrap run.

## 7. Risk · 风险

| Risk · 风险 | Mitigation · 缓解 |
|---|---|
| PhyloPic API changes again before user bootstrap | Code uses field names from 2026-05 smoke test; if API drifts, script logs detailed `[api error]` and continues other sources |
| NIH BioArt manual-download mode requires user action | README says so explicitly; `--source nih_bioart` without `--zip` prints clear instruction |
| ~17000 element bootstrap takes >1 night | `--max-per-source` flag lets user cap PhyloPic to e.g. 1000 |
| Template SVG won't render on older Inkscape | Template uses only SVG 1.1 features; tested in Inkscape 1.0+ |
| Bilingual PR description triggers CodeRabbit confusion | Each section has a clear `English / 中文` separator |

## 8. Commit plan · 5-commit map

| # | Commit · 提交 | Files · 文件 |
|---|---|---|
| 1 | `feat(sci-pipeline): add CC0 bootstrap downloader / 添加 CC0 元件库批量下载脚本` | `scripts/download_cc0_seed.py` |
| 2 | `docs(sci-pipeline): add bootstrap quickstart README / 加入 bootstrap 快速上手 README` | `README.md` |
| 3 | `feat(sci-pipeline): library tools with unified index, preview, export, attribution / 元件库工具：统一索引 / 预览 / 导出 / 授权` | `scripts/library_tools.py` |
| 4 | `feat(sci-pipeline): figure assembler with ID resolver + auto attribution / figure 组装器：ID 解析 + 自动授权` | `scripts/assemble_figure.py`, `templates/dark_proteome_4panel_template.svg`, `templates/dark_proteome_manifest.yaml` |
| 5 | `feat(sci-pipeline): SKILL.md v0.1.1 — LLM-writes-SVG repath + CC0 bootstrap chapter / SKILL.md v0.1.1：LLM 直出 SVG 路径修正 + CC0 引导章节` | `SKILL.md` |

OpenSpec status moves `proposed → approved` after merge.
