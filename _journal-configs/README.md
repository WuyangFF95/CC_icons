# Per-journal figure specs

Each YAML file in this directory codifies one journal's published figure
requirements (sizes, fonts, line weights, etc.). `assemble_figure.py
--journal <name>` reads `<name>.yaml` here and validates the output SVG
against it before declaring success.

每个 YAML 文件编码一个期刊的发表 figure 规范（尺寸 / 字体 / 最小线宽
等）。`assemble_figure.py --journal <name>` 读取本目录的 `<name>.yaml`
并在声明成功前对输出 SVG 做校验。

## Schema

```yaml
name:       Human-readable journal name
publisher:  Publishing house
url:        Author guidelines URL (for traceability)

sizes:
  single_column: { width_mm: 89,  max_height_mm: 220 }
  double_column: { width_mm: 183, max_height_mm: 240 }

fonts:
  preferred: [Arial, Helvetica]      # warn if missing
  forbidden: [Comic Sans, Papyrus]   # fail if found

font_size:
  min_pt: 7        # element labels
  max_pt: 12

line_weight:
  min_pt: 0.5      # minimum stroke width

color_mode: RGB | CMYK
file_formats: [pdf, eps, ai]

notes: |
  Any per-journal peculiarities a reader should know about.
```

## Lookup

`assemble_figure.py --journal nature-reviews-drug-discovery` resolves to
`_journal-configs/nature-reviews-drug-discovery.yaml`. Names are
slugified — keep filenames `[a-z0-9-]+`.

## Validation severity

| Field | Violation severity |
|---|---|
| `fonts.forbidden` matched | **error** (fail build) |
| `font_size.min_pt` violated | **error** |
| `line_weight.min_pt` violated | **error** |
| `fonts.preferred` not used | warning |
| `sizes.*` exceeded | warning (depends on journal) |

## Sources

The starter configs in this directory transcribe author-guideline pages
that were public as of 2026-05. Re-verify against the live page before
relying on a config for a real submission.

本目录的初始配置是从 2026-05 时公开的 author-guideline 页面转录的。
真实投稿前请对当时活页面再次核对。
