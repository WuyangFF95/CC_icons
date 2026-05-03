# Project palettes

Each YAML file here is a list of hex swatches that
`scripts/lowfi_trace.py --palette <file>` will snap every `fill` /
`stroke` / `stop-color` to. The starter palettes match the
`dark_proteome_4panel_template.svg` panel headers — pick one closest to
your project's visual direction and edit.

每份 YAML 是一组 hex 色板，`scripts/lowfi_trace.py --palette <file>`
会把所有 `fill` / `stroke` / `stop-color` 吸附到色板中最近的色。起步
色板对齐 `dark_proteome_4panel_template.svg` 的面板头颜色 —— 选最贴
近你项目视觉方向的，再按需改。

## Schema

```yaml
name: Human-readable palette name
notes: |
  Where this palette comes from and what it's good for.
colors:
  - "#1B5BA0"
  - "#2E7CD6"
  - ...
```

## Pick by panel

- **nature-flat-blue**: cool blue-only, Nature-style schematics
- **dark-proteome-mixed**: blue + green + gray + purple (matches the
  bundled 4-panel template)
- **clinical-warm**: warm grays for clinical / NEJM-leaning figures

To add your own, drop a new YAML following the schema above. Names are
slugified — keep filenames `[a-z0-9-]+`.
