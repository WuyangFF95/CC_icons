---
name: scientific-illustration-asset-pipeline
version: 0.1.1
description: |
  Master skill for scientific figure creation via AI-generated element library
  plus semi-automated assembly. As of v0.1.1, the primary path for *whole-figure*
  generation is "LLM writes SVG code directly" (Claude / MiniMax / GLM-5.1 /
  GPT-5), supplemented by a CC0 icon library (~17000 elements from BioIcons,
  PhyloPic, NIH BioArt, Reactome, SciDraw, Servier Medical Art) for the
  semi-3D bio-element gap that LLMs cannot draw. Recraft V3 is repositioned
  as element-level reinforcement only. Triggers: "做 Fig1", "graphical
  abstract", "组装科研图", "用元件库做综述图", "scientific figure assembly",
  "TOC graphic", "review paper schematic".
  This skill orchestrates other skills (recraft-scientific-illustration for
  vector elements) and MCP servers (inkscape-mcp, office-powerpoint-mcp).
allowed-tools:
  - mcp__recraft__generate_image
  - mcp__recraft__vectorize_image
  - mcp__zhipu__generate_image
  - mcp__gemini__gen_image
  - mcp__minimax__gen_image
  - mcp__openai__create_image
  - mcp__inkscape__action_run
  - mcp__inkscape__dom_set
  - mcp__inkscape__dom_clean
  - mcp__powerpoint__create_presentation
  - mcp__powerpoint__add_slide
  - mcp__powerpoint__add_image
  - mcp__powerpoint__add_text_box
  - bash
  - create_file
  - str_replace
  - view
---

# Scientific Illustration Asset Pipeline v0.1

## v0.1.1 重大变更摘要（必读）

本版基于一次实战测试做了路径修正。**v0.1.0 把 Recraft 当做整图主力**——这条路在面对"科研图必须文字真节点"时被证伪。修正项：

| 章节 | v0.1.0 | v0.1.1 修订 |
|---|---|---|
| 整图主力 provider | Recraft V3 + 矢量化 | **LLM 直出 SVG 代码**（**MiniMax M-series 首选**：量大管饱 / 清晰 / 色块少 / 比 Recraft 便宜 / 适合带文字 + placeholder；备选 Claude / GLM-5.1 / GPT-5）|
| Recraft 定位 | 整图 + 元件 | **元件补强**（α-helix / 受体 / 立体器官等半立体元件）|
| 文字处理 | 提示词避免 | **LLM 路径天然真节点**，仅需 Inkscape 修文字碰撞 |
| 排版自动化 Level 3 | 标"完全自动化不可能" | **限定为"扩散模型路径不可能"**，LLM 路径 + 元件库可行 |
| 元件库 bootstrap | 手动一个个生成 | **CC0 公开数据源 6 选一晚跑完，~17000 元件入库** |
| 投稿 attribution | 未处理 | **assemble_figure.py 自动注入 + library_tools attribution 命令** |

**核心洞察**：LLM 写 SVG 代码（不是图像扩散模型）是科研图的真正赛道。失败模式（文字坐标重叠）比扩散路径的失败模式（文字必糊）易修复 100 倍。详见 `## 一、AI Provider 选择矩阵 § 1.0`。

---

## 零、CC0 元件库 Bootstrap（v0.1.1 新增）

**任何项目第一步**：从 6 个 CC0 / CC-BY / Public Domain 数据源批量下载 ~17000 个矢量科研元件入库，作为后续 figure 组装的"立体元件子弹"。这一步替代 v0.1.0 里"为每个项目单独 Recraft 生成"的低效流程。

### 0.1 数据源清单

| 源 | 授权 | 规模 | 拿法 |
|---|---|---|---|
| **BioIcons** | **CC0** | ~1500 SVG | `git clone duerrsimon/bioicons` |
| **PhyloPic** | CC0 / CC-BY | ~10000 silhouettes | REST API `api.phylopic.org` |
| **NIH BioArt** (NIAID) | **公有领域** | ~2500 illustrations | manual ZIP drop（站点已是 Next.js SPA，无公开 REST 端点）|
| **Reactome Icons** | CC BY 4.0 | ~500 SVG | `git clone reactome/icon-lib` |
| **SciDraw** (Janelia) | **CC0** | ~700 SVG | webscrape `scidraw.io` |
| **Servier Medical Art** | CC BY 3.0 | ~3000 SVG/EMF | 用户手动下 PPTX 包，脚本拆 |

### 0.2 一晚 bootstrap 命令

```bash
# 一次性全部下载（一晚跑完，~500MB）
python scripts/download_cc0_seed.py --all --target ~/sci-illustration-library

# 或限速版（PhyloPic 量大可限）
python scripts/download_cc0_seed.py --source phylopic --max-per-source 1000

# Servier 需要先手动从 https://smart.servier.com 下 PPTX 包
python scripts/download_cc0_seed.py --source servier \
       --pptx ~/Downloads/servier-anatomy.pptx

# NIH BioArt 站点已是 Next.js SPA（无 REST 端点，HTTP 抓取拿不到任何卡片）。
# 流程：在 https://bioart.niaid.nih.gov/ 浏览并选下载所需 illustration，
# 打成 ZIP 后投放：
python scripts/download_cc0_seed.py --source nih_bioart \
       --zip ~/Downloads/nih_bioart_pack.zip
```

### 0.3 入库结构

```
~/sci-illustration-library/
├── library/
│   ├── index.json                    ← 元数据 + 授权追溯（统一索引）
│   ├── cells/
│   │   ├── immune/                   ← T cell, macrophage, dendritic
│   │   ├── cancer/
│   │   ├── neuron/
│   │   └── general/
│   ├── molecules/
│   │   ├── protein/
│   │   ├── nucleic_acid/
│   │   └── small/
│   ├── organelles/
│   ├── tissues/
│   ├── organs/
│   ├── equipment/
│   │   ├── lab/
│   │   └── clinical/
│   └── pathways/
├── _raw/                             ← 原始下载（git repos, PPTX 解包）
└── _final/                           ← 项目最终交付目录
    └── <project-name>/
```

### 0.4 元数据 schema（`library/index.json`）

每个 icon 一条记录：

```json
{
  "id": "bioicons-tcell-activated",
  "name": "T cell activated",
  "tags": ["immune", "lymphocyte"],
  "category": "cells/immune",
  "source": "bioicons",
  "source_url": "https://bioicons.com",
  "license": "CC0",
  "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
  "attribution_required": false,
  "file": "cells/immune/bioicons-tcell-activated.svg",
  "added_at": "2026-05-02"
}
```

CC BY 类型自动 `attribution_required: true` 并填充 `attribution` 字段。投稿前用 `library_tools.py attribution` 一键导出 figure caption 文本。

### 0.5 浏览与搜索

```bash
# 整库统计
python scripts/library_tools.py stats

# 搜索元件
python scripts/library_tools.py search "alpha helix"
python scripts/library_tools.py search "T cell" --license CC0 --max 20
python scripts/library_tools.py search --category cells/immune

# 缩略图 grid 浏览
python scripts/library_tools.py preview --query "neuron" --output preview.png

# 导出选定元件到 PPTX 模板（每元件一 slide，含名称和授权）
python scripts/library_tools.py export \
       --ids "bioicons-alpha-helix,phylopic-abc12345" \
       --output ./elements.pptx

# 投稿前导出 attribution 清单
python scripts/library_tools.py attribution --output ./fig1-attribution.md
```

### 0.6 与 Recraft 的关系

CC0 库 ≠ 完全替代 Recraft。两者分工：

| 元件类型 | 优先来源 |
|---|---|
| 通用细胞、抗体、酶等扁平 icon | **CC0 库**（BioIcons / Reactome 优先） |
| 通用 silhouette（动物、人、植物） | **CC0 库**（PhyloPic） |
| 神经元、电极等 neuro 专用 | **CC0 库**（SciDraw） |
| 解剖图、医疗设备 | **CC0 库**（Servier / NIH BioArt） |
| **半立体 cartoon**（α-helix 螺旋柱、HLA-I receptor、立体器官切面） | **Recraft V3** + style reference |
| 极特殊原创元件 | **手画**（Affinity Designer） |

**新工作流**：先去 `library_tools.py search` 查 CC0 库，**找不到合适的再 Recraft 补**。这样平均每个项目 Recraft 调用次数 ↓ 90%，速度↑、成本↓、license 更干净。

---

这个 skill 解决的核心问题是 **科研图制作的工程化**：
把每张图从"一次性手工艺品"变成"基于元件库的可组装、可版本化、可复用产物"。

```
[需求]
  |  "我要做一张 HCC 空间异质性 Fig1"
  v
[第 1 层] 元件清单分析
  |  分解为：肝细胞、Treg、CD8 T、HCC 组织、空间组学示意箭头...
  v
[第 2 层] 元件来源决策
  |  库存有？  -> 直接取
  |  库存无？  -> AI 生成 (按场景选 provider)
  |          -> 矢量化 (如果不是 SVG)
  |          -> 质检入库
  v
[第 3 层] 排版决策（人主导）
  |  视觉层级、信息流方向、留白节奏 -> 用户拍板
  v
[第 4 层] 自动化组装（机器主导）
  |  Inkscape MCP / PowerPoint MCP 调用
  |  填充元件、对齐网格、应用色板、加文字标签
  v
[第 5 层] 输出与归档
  |  SVG (编辑) + PDF (投稿) + PPTX (汇报)
  |  元件库 git 提交
```

---

## 一、AI Provider 选择矩阵

不同模型在不同场景的实测能力差异显著。**选错 provider 是浪费 credit 的最大原因**。

### 1.0 整图 vs 元件：两条根本不同的赛道（v0.1.1 重要修订）

科研图生成任务分两层，**用错赛道一定失败**：

```
┌─────────────────────────────────────────────────────────────┐
│  整图 / 4-panel Fig1 / graphical abstract                   │
│  ────────────────────────────────────                       │
│  → 走 LLM 写 SVG 代码 (第一档)                                │
│    Claude / MiniMax M-series / GLM-5.1 / GPT-5              │
│    优势：文字真节点、节点极少、可编辑、Git diff 友好           │
│    限制：文字坐标重叠、半立体元件画不精细                       │
│    修复：Inkscape 5-10 分钟微调 / 元件库补                    │
│                                                             │
│  → ❌ 不走 扩散模型 + 矢量化 (Recraft 整图)                    │
│    会得到：2308 path / 0 text / 文字全糊（实测过）             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  单个半立体生物元件（α-helix / receptor / cell cartoon）        │
│  ─────────────────────────────                              │
│  → 走 元件库优先（CC0 → Recraft V3 → 手画）                    │
│    扁平 icon → CC0 库已覆盖 80%                                │
│    半立体 cartoon → Recraft V3（必须 prompt 禁文字）           │
│    极特殊原创 → Affinity Designer 手画                         │
└─────────────────────────────────────────────────────────────┘
```

**实测数据**（同主题 "dark proteome" 4-panel 对比）：

| Provider | 路径 | `<text>` 节点 | `<path>` | 节点总数 | 文件大小 |
|---|---|---|---|---|---|
| Recraft V3 | 扩散 → 矢量化 | **0**（文字必糊） | 2308 | 44661 | 1.8 MB |
| MiniMax M2.5 | LLM 写代码 | **142** | 31 | 150 | 41 KB |
| Claude Visualizer | LLM 写代码 | ~80 | ~25 | ~80 | ~36 KB |
| Claude 独立 SVG | LLM 写代码 | **166** | 24 | 111 | 36.5 KB |

LLM 路径在所有维度都更好（除半立体元件视觉质感的 30%，由元件库补强）。

### 1.0a LLM 直出 SVG（整图主力 prompt 模板）

```
Generate a complete SVG figure for [TOPIC], Nature Reviews Drug Discovery
style. 4-panel layout (a/b/c/d), central theme circle, bottom legend strip.
ViewBox 1600×1000. Constraints:
- All text as real <text> nodes (must be editable, not paths)
- Flat colors, no gradients
- Color-coded panel headers (a=blue, b=green, c=gray, d=purple)
- Each panel has bold title + subtitle + 4-6 child elements
- Output ONLY the SVG code, no markdown fence
```

#### 写 SVG 代码的 LLM provider 选型

| Provider | 文字 | 节点密度 | 量大管饱? | 单价倾向 | 推荐场景 |
|---|---|---|---|---|---|
| **MiniMax M-series** | 真节点（中英都强） | 低（色块少、清晰） | ✅ 量大管饱 | **比 Recraft 便宜** | **首选**：含**文字 + placeholder** 的 SVG / PDF（投稿 figure / TOC / graphical abstract）|
| Claude (Visualizer / 直接对话) | 真节点 | 低 | ⚠️ 视套餐 | 中 | 复杂语义（如 "把这条 pathway 重新组织"），需要推理时 |
| GLM-5.1 | 真节点（中文极强） | 中 | ✅ | 低 | 中文标题/标注密集场景 |
| GPT-5 | 真节点 | 中 | ⚠️ 视套餐 | 中-高 | 需精细 prompt 控制 / 严格遵照规格时 |

**MiniMax 主战场**：当一张图里包含**多个文字标签 + 待填 placeholder + 图形元件**时（即典型 4-panel figure），MiniMax 既能保证文字真节点，又能把色块画得简洁规范，且**单价低，可批量重试**——比 Recraft 整图链便宜多倍，是 v0.1.1 默认推荐路径。

⚠️ **MiniMax 不做 PNG → SVG 矢量化**。如需把扩散模型出来的 PNG 转 SVG，走 §二（vectorizer.ai / Inkscape Trace），不要让 MiniMax 接 PNG 输入。

修复文字碰撞（LLM 路径的固有失败模式）：

```bash
# Inkscape GUI 微调，5-10 分钟
inkscape figure.svg

# 或用脚本检测 bbox 冲突（待 v0.2 实现）
# python scripts/text_collision_check.py figure.svg
```

### 1.1 单元件 provider（按元件类型）

> 这一节只覆盖**单元件**（cell / receptor / enzyme 之类的图标素材）。整图（Fig1 / TOC / graphical abstract）走 §1.0a 的 LLM 写 SVG 路径，**MiniMax / Claude / GLM-5.1 / GPT-5 是整图主力，不在本表中重复**。

| 元件类型 | 首选 | 备选 | 避免 |
|---|---|---|---|
| 抽象生物元件（细胞、蛋白）| **Recraft V3** (vector) | Gemini Imagen 4 | OpenAI DALL-E |
| 写实解剖结构（器官、组织）| **Gemini Imagen 4** | OpenAI gpt-image | Recraft (太抽象) |
| 中文标签 / 中文场景（PNG 路径）| **智谱 CogView-4** | OpenAI gpt-image | Recraft (中文糊) |
| 实验装置（仪器、培养皿）| **Recraft V3** | Gemini Imagen 4 | - |
| 信号通路单点（受体激活等）| **Recraft V3** | - | - |
| 流程图节点（圆角矩形等）| **不要用 AI** | 直接用 Inkscape/PPT 画 | 任何 AI |
| 手绘风/插画风 | OpenAI gpt-image | Gemini | Recraft (太程式化) |

> **MiniMax 不在本表里**：MiniMax 没有 PNG → SVG 矢量化能力，强项是直接写 SVG 代码（见 §1.0a）。把它当 PNG 元件 provider 用是错配；中文 PNG 元件场景由 CogView-4 覆盖即可。

### 1.2 按输出格式区分

把"出 SVG"拆成两条**根本不同**的产能：扩散模型直出 vs LLM 写代码。前者文字必糊（v0.1.1 已实证），后者文字真节点。两个不能合并成一列。

| Provider | LLM 写 SVG 代码 | 扩散直出 SVG | 直出 PNG | 中文 | style ref |
|---|---|---|---|---|---|
| **MiniMax M-series** | ✅ **强**（量大管饱、清晰、色块少、便宜） | ❌ | ✅ | **强** | ❌ |
| Claude (Visualizer / 对话) | ✅ 强（推理强） | ❌ | ❌ | 强 | ❌ |
| GLM-5.1 | ✅ 中 | — | ✅（CogView-4）| **强** | ❌ |
| GPT-5 | ✅ 中（按规格严谨） | ❌ | ✅（gpt-image）| 中 | ❌ |
| Recraft V3 | ❌ | ✅（**文字必糊**，仅适合无字元件）| ✅ | 弱 | ✅ 强 |
| Gemini Imagen 4 | ❌ | ❌ | ✅ | 中 | ⚠️ via reference image |
| OpenAI gpt-image | ❌ | ❌ | ✅ | 中 | ❌ |

**关键推论（v0.1.1 修订）**：
- 整图走「LLM 写 SVG 代码」第一档（MiniMax 首选，便宜+量大），文字真节点 → 直接可投稿；
- 单元件走「扩散直出 SVG / PNG → 矢量化」（Recraft V3 / Gemini / CogView-4），但**禁文字**；
- 同一 provider 两种产能不要混用（不要让 Recraft 出整图，不要让 MiniMax 接 PNG 矢量化）。

### 1.3 MCP 端点配置参考

```json
{
  "mcpServers": {
    "recraft": {
      "url": "https://mcp.recraft.ai/mcp",
      "headers": {"Authorization": "Bearer ${RECRAFT_API_KEY}"}
    },
    "universal-image": {
      "command": "uvx",
      "args": ["universal-image-generator-mcp"],
      "env": {
        "GEMINI_API_KEY": "${GEMINI_API_KEY}",
        "ZHIPU_API_KEY": "${ZHIPU_API_KEY}",
        "OUTPUT_IMAGE_PATH": "${HOME}/sci-illustration-library/_raw"
      }
    },
    "human-mcp": {
      "command": "npx",
      "args": ["-y", "@mrgoonie/human-mcp"],
      "env": {
        "GOOGLE_GEMINI_API_KEY": "${GEMINI_API_KEY}",
        "MINIMAX_API_KEY": "${MINIMAX_API_KEY}",
        "ZHIPU_API_KEY": "${ZHIPU_API_KEY}"
      }
    },
    "inkscape": {
      "command": "inkscape-mcp",
      "env": {
        "INKS_WORKSPACE": "${HOME}/sci-illustration-library",
        "INKS_MAX_FILE": "52428800"
      }
    },
    "powerpoint": {
      "command": "uvx",
      "args": ["office-powerpoint-mcp-server"],
      "env": {
        "PPT_TEMPLATE_PATH": "${HOME}/sci-illustration-library/_templates"
      }
    }
  }
}
```

**WPS 不在列**——目前没有可用的 WPS MCP。需要 WPS 兼容时，
直接用 PowerPoint MCP 生成 .pptx，WPS 能正常打开。

---

## 二、PNG → SVG 矢量化层

> **本节只针对**走"扩散模型 / 栅格 provider 出 PNG → 转 SVG"路径的**单元件**生成。
>
> **MiniMax / Claude / GLM-5.1 / GPT-5 不走这一节** —— 它们直接撰写 SVG 代码（§1.0a），既没有 PNG → SVG 矢量化能力，也不需要这条链。把 MiniMax 的 PNG 输出再去走 vectorizer.ai 是错配。
>
> 本节适用：Recraft V3 (vector 端点之外的) / Gemini Imagen 4 / OpenAI gpt-image / 智谱 CogView-4 出的 PNG。

非 LLM-写-SVG 来源的元件必须经此步骤。

### 矢量化方案对比

| 方案 | 质量 | 成本 | 适用 |
|---|---|---|---|
| **vectorizer.ai API** | 高 | $0.20/张 | 主力方案 |
| Inkscape Trace Bitmap (CLI) | 中 | 免费 | 兜底，适合简单线稿 |
| Recraft vectorize 端点 | 中 | 0.005 credit | 已有 Recraft key 时用 |
| svg-trace (Python) | 低 | 免费 | 只能黑白 |

### 矢量化前的图像预处理（关键）

直接矢量化 AI 生成的 PNG 会出现"沾水"问题（抗锯齿过渡 → 半透明色层）。
**必须先做以下预处理**：

```python
def preprocess_for_vectorization(png_path: Path, output_path: Path) -> None:
    """
    AI PNG 矢量化前的预处理：
    1. 色调分离（quantize 到 8-12 色）
    2. 边缘锐化
    3. 透明背景标准化
    """
    from PIL import Image, ImageFilter
    img = Image.open(png_path).convert('RGBA')

    # 色调分离：减少颜色数量
    rgb = img.convert('RGB')
    quantized = rgb.quantize(colors=10, method=Image.Quantize.MEDIANCUT)
    rgb_q = quantized.convert('RGB')

    # 重新合并 alpha 通道
    a = img.split()[-1]
    final = Image.merge('RGBA', (*rgb_q.split(), a))

    # 锐化边缘
    final = final.filter(ImageFilter.UnsharpMask(radius=1, percent=120))

    final.save(output_path, 'PNG')
```

预处理后再送 vectorizer.ai：节点数能减少 60–80%，颜色规范回到可控范围。

---

## 三、元件库结构与管理

### 3.1 目录结构

```
~/sci-illustration-library/
|
|-- _style-references/             # 风格锚点
|   |-- nature-flat-blue.svg
|   |-- cell-semi3d-warm.svg
|   `-- lancet-clinical.svg
|
|-- _templates/                    # PPT/SVG 排版模板
|   |-- fig1-2x2-panel.svg
|   |-- graphical-abstract-1x1.svg
|   |-- review-flow-3-tier.svg
|   `-- toc-square.pptx
|
|-- _raw/                          # AI 原始输出（含未矢量化 PNG）
|   |-- recraft/
|   |-- gemini/
|   `-- zhipu/
|
|-- cells/                         # 已质检入库的细胞元件
|   |-- _index.yaml                # 索引文件
|   |-- hepatocyte-flat-v1.svg
|   |-- treg-flat-v1.svg
|   |-- cd8-tcell-flat-v1.svg
|   `-- macrophage-m1-flat-v1.svg
|
|-- molecules/                     # 分子元件
|   |-- _index.yaml
|   |-- dna-double-helix-v1.svg
|   |-- antibody-igg-v1.svg
|   `-- mrna-strand-v1.svg
|
|-- organelles/                    # 细胞器
|-- tissues/                       # 组织
|-- organs/                        # 器官
|-- equipment/                     # 实验装置
|-- pathways/                      # 信号通路单点
|-- arrows/                        # 自定义箭头/连接符（手画）
`-- _final/                        # 已发表的最终 figure（归档）
    `-- 2026-yang-hcc-spatial/
        |-- fig1.svg
        |-- fig1.pdf
        `-- elements-used.yaml      # 引用元件列表（可复现性）
```

### 3.2 命名约定

```
{subject}-{style}-{version}.{ext}

例:
  treg-flat-v1.svg          v1 版本，扁平风
  hepatocyte-semi3d-v2.svg  v2 版本，半 3D
  igg-cartoon-v1.svg        v1 版本，cartoon 风
```

**禁止**：空格、中文字符、特殊符号、版本号缺失。

### 3.3 元件 metadata（_index.yaml）

每个分类目录下维护一个 `_index.yaml`：

```yaml
# cells/_index.yaml
elements:
  - file: hepatocyte-flat-v1.svg
    subject: hepatocyte
    style: flat-blue
    style_ref: nature-flat-blue.svg
    provider: recraft-v3
    style_id: sty_abc123              # Recraft style ID
    generated_at: 2026-05-02
    nodes: 87                          # 路径节点数
    colors: 5                          # 颜色数
    qc_passed: true
    used_in:
      - 2026-yang-hcc-spatial/fig1.svg
    license: research-use-only

  - file: treg-flat-v1.svg
    subject: regulatory T cell
    style: flat-blue
    style_ref: nature-flat-blue.svg
    provider: recraft-v3
    style_id: sty_abc123
    generated_at: 2026-05-02
    nodes: 124
    colors: 4
    qc_passed: true
    used_in: []
    license: research-use-only
```

### 3.4 版本控制

```bash
cd ~/sci-illustration-library
git init
git add .
git commit -m "init library"

# 之后每加一个元件
git add cells/treg-flat-v1.svg cells/_index.yaml
git commit -m "feat(cells): add treg-flat-v1"
```

这把元件库变成可追溯、可恢复、可分享的资产，符合"复利"原则。

---

## 四、排版自动化分级

### Level 0: 全手动
打开 Affinity Designer，拖拽元件，手动布局。

**适用**：第一次做某类图、视觉创意要求高、元件数 > 20。
**自动化收益**：低，不要强行自动化。

### Level 1: 模板填充（半自动）
预先设计好 SVG 模板（含 placeholder），用 Inkscape MCP 替换 placeholder 为实际元件。

**适用**：固定布局的复刻（如每周组会汇报模板、批量同类 Fig）。
**自动化收益**：高，每张图省 30 分钟。

```python
# 用 Inkscape MCP 替换模板中的 placeholder
mcp__inkscape__dom_set(
    file="_templates/fig1-2x2-panel.svg",
    selector="#panel-a-element",
    attribute="xlink:href",
    value="../cells/hepatocyte-flat-v1.svg"
)
```

### Level 2: 智能排版（机器辅助）
基于约束求解：给定元件 + 布局规则（对齐、留白、层级），自动出多个候选。

**适用**：探索阶段，让 AI 出 3–5 个布局供你挑选。
**自动化收益**：中，加快迭代但不替代决策。

### Level 3: 完全自动化
基于自然语言描述出最终图。

**v0.1.1 修订**：v0.1.0 标"架构上不可能"，过严。

**实际可行性按路径分**：
- ❌ **扩散模型路径**（"用 Imagen / Recraft 直接出 Fig1 PNG"）：仍架构不可能。文字必糊。
- ⚠️ **LLM 写 SVG + 元件库自动填充路径**：**可达 70-80% 投稿质量**。瓶颈在文字坐标重叠 + 半立体元件细节。
- ✅ **LLM + 元件库 + 5 分钟人工微调**：可达 95% 投稿质量，等价于 BioRender + Affinity 手做的 90% 时间。

**新结论**：投稿 figure 推荐 Level 1+2 混合（LLM 写骨架 + 元件库填充 + 人工 polish），不再禁止 Level 3 探索。

---

## 五、Inkscape MCP 排版工作流

### 5.1 标准流程

```python
# Step 1: 创建新 SVG 画布（标准 A4 横向 = Fig1 常见尺寸）
mcp__inkscape__action_run(
    actions="file-new;canvas-size-A4-landscape"
)

# Step 2: 导入模板布局
mcp__inkscape__action_run(
    actions=f"import:{TEMPLATE_PATH}/fig1-2x2-panel.svg"
)

# Step 3: 逐 panel 填充元件
for panel_id, element_path in panel_assignments.items():
    mcp__inkscape__dom_set(
        selector=f"#{panel_id} use[data-placeholder]",
        attribute="xlink:href",
        value=element_path
    )

# Step 4: 添加文字标签（用户提供内容，工具负责对齐）
for label_id, text_content in labels.items():
    mcp__inkscape__dom_set(
        selector=f"#{label_id}",
        text=text_content,
        attribute="font-family",
        value="Arial"
    )

# Step 5: 应用对齐与分布
mcp__inkscape__action_run(
    actions="select-all;align-horizontal-center;distribute-vertical-equal"
)

# Step 6: 清理与优化
mcp__inkscape__dom_clean(file=output_path)  # 调用 scour 优化

# Step 7: 多格式导出
mcp__inkscape__action_run(
    actions=f"export-filename:{output_path}.pdf;export-pdf-version:1.5;"
            f"export-text-to-path:false;export-do"
)
```

### 5.2 文字标注的边界

**机器可以做**：
- 应用统一字体、字号、颜色
- 对齐（左/右/居中、顶/底/中）
- 等距分布

**机器不能做**（必须人决定）：
- 标注内容（"Treg" vs "Foxp3+ Treg" vs "regulatory T cell"）
- 标注位置（避免遮挡、引导视线）
- 标注层级（panel 标题 vs 元件标签 vs 注释）
- 缩写规范（首次出现是否定义）

实践中：**用户口述内容 → MCP 排版** 是最优分工。

---

## 六、PowerPoint MCP 工作流

### 何时用 PPT 而不是 SVG/PDF

| 场景 | 推荐格式 |
|---|---|
| 投稿期刊 | PDF (来自 SVG) |
| 实验室组会汇报 | PPTX |
| 与导师协作改稿 | PPTX (导师习惯) |
| Poster 展示 | PDF (来自 Affinity Publisher) |
| 网页/社交媒体 | SVG/PNG |
| 答辩 | PPTX |

### 6.1 标准流程

```python
# Step 1: 从模板创建演示文稿
mcp__powerpoint__create_presentation_from_template(
    template_path="_templates/group-meeting.pptx",
    output_path="_final/2026-05-02-treg-update.pptx"
)

# Step 2: 添加 figure slide
slide_idx = mcp__powerpoint__add_slide(
    layout="title_and_content",
    title="HCC 空间异质性 Fig1"
)

# Step 3: 嵌入 SVG 元件（PPT 内部转 EMF 矢量）
mcp__powerpoint__add_image(
    slide_index=slide_idx,
    image_path="_final/2026-yang-hcc-spatial/fig1.svg",
    left_inches=0.5, top_inches=1.0,
    width_inches=9.0, height_inches=5.5
)

# Step 4: 添加注释文字框
mcp__powerpoint__add_text_box(
    slide_index=slide_idx,
    text="左 panel: tumor core 高 Treg 密度；右 panel: invasive front 混合免疫群体",
    left_inches=0.5, top_inches=6.8,
    width_inches=9.0, height_inches=0.5,
    font_name="思源黑体",
    font_size=10
)

# Step 5: 保存
mcp__powerpoint__save_presentation(path="_final/2026-05-02-treg-update.pptx")
```

### 6.2 PPT 与 SVG 互通

PPT 里嵌入 SVG，导出后 SVG 仍可在 Inkscape/Affinity 编辑——
但要注意 PPT 自身的文字框**不会**变成 SVG 的 `<text>`，导出 SVG 时会被栅格化。

**结论**：PPT 用于汇报、SVG/PDF 用于投稿。两套并行维护，不要试图统一。

---

## 七、决策树：什么场景用什么

```
我要做一张科研图
│
├─ 是数据图表（柱、线、散点、热图、生存曲线）？
│    └→ 用 matplotlib/ggplot 直接出 SVG/PDF，不用本 skill
│
├─ 是流程图（决策树、算法流程、时间线）？
│    └→ 用 Mermaid/Graphviz 直接出 SVG，不用本 skill
│
├─ 是化学结构式？
│    └→ 用 ChemDraw/RDKit，不用本 skill
│
├─ 是蛋白 3D 结构？
│    └→ 用 PyMOL/ChimeraX，不用本 skill
│
├─ 是综述/Fig1/graphical abstract（含示意元件 + 文字标注）？
│    └→ 进入本 skill 流程：
│       │
│       ├─ Step 1: 列元件清单
│       ├─ Step 2: 库内查询，缺失项进入生成
│       │   └→ 调用 recraft-scientific-illustration skill
│       │      （或对应其他 provider 的元件生成 skill）
│       ├─ Step 3: 矢量化 + 质检 + 入库
│       ├─ Step 4: 用户决定布局（不要让 AI 决定）
│       ├─ Step 5: Inkscape MCP 自动化排版
│       ├─ Step 6: 用户提供文字内容，MCP 应用样式
│       └─ Step 7: 导出 SVG + PDF + (可选) PPTX
│
└─ 是其他场景？
     └→ 退回手画
```

---

## 八、典型项目示例

**项目**：HCC 空间异质性综述 Fig1，2×2 panel，Nature Reviews Drug Discovery 风格。

**元件清单**（用户提出）：

| Panel | 需要元件 | 来源 |
|---|---|---|
| a (Tumor core) | 肝细胞 ×3、Treg ×2、CD8 T ×1、肿瘤血管 ×1 | 库内已有 4 个 + 新生成 3 个 |
| b (Invasive front) | 肝细胞 ×2、Treg ×1、CD8 T ×3、巨噬 M2 ×2 | 库内已有 |
| c (Spatial transcriptomics) | Visium 阵列示意、热图色块 | 新生成 + matplotlib |
| d (Therapeutic target) | 抗体 ×1、受体 ×1、信号通路点 ×3 | 库内已有 + 新生成 1 个 |

**执行**：

```
1. 检查元件库
   bash: python tools/check-library.py --project hcc-spatial
   → 缺失：tumor-vasculature, m2-macrophage, visium-array

2. 生成缺失元件
   recraft-scientific-illustration:
     - tumor-vasculature-flat-v1.svg
     - m2-macrophage-flat-v1.svg
     - visium-array-flat-v1.svg
   全部用 style_id=sty_nature_blue 锁风格

3. 质检入库（自动）
   全部 PASS，元件入 cells/ molecules/ equipment/ 各目录
   git commit -m "feat: add hcc-spatial fig1 elements"

4. 用户决策（人决定）：
   - 信息流方向：左→右
   - panel a 强调密度对比，用网格背景
   - panel d 用箭头收束到中央 target
   - 字体：Arial Bold 14pt 标题，Arial 10pt 标签
   - 配色锁定 5 色（nature-flat-blue 色板）

5. Inkscape MCP 自动化排版
   - 加载 _templates/fig1-2x2-panel.svg
   - 填充元件、应用色板、对齐
   - 文字框留白等待用户填内容

6. 用户口述文字内容，MCP 写入

7. 导出
   - fig1.svg (编辑用)
   - fig1.pdf (投稿用)
   - fig1.pptx (与导师讨论用)

8. 归档
   _final/2026-yang-hcc-spatial/
     ├─ fig1.{svg,pdf,pptx}
     ├─ elements-used.yaml
     └─ generation-log.md
   git commit -m "feat(figures): hcc-spatial Fig1 v1"
```

---

## 九、版本历史与限制

### v0.1.1（current）新增
- **零章 CC0 元件库 bootstrap**：6 数据源 ~17000 元件一键下载
- **download_cc0_seed.py**：BioIcons / PhyloPic / NIH BioArt / Reactome / SciDraw / Servier
- **library_tools.py 升级**：`stats` / `preview` (缩略图 grid) / `export` (PPTX) / `attribution` 4 个新命令
- **统一索引**：`library/index.json` 合并 CC0 bulk + legacy YAML
- **assemble_figure.py 升级**：支持按 unified ID 引用元件、自动注入 CC BY attribution、PNG/EMF 元件回退到 `<image>` 引用
- **示例模板与 manifest**：`templates/dark_proteome_4panel_template.svg` + 配套 manifest YAML
- **整图主力 provider 修订**：从 Recraft 改为 LLM 直出 SVG（Claude / MiniMax / GLM-5.1 / GPT-5）
- **Recraft 重新定位**：仅做半立体元件补强，不再担任整图主力
- **Level 3 评估更新**：撤回"完全自动化不可能"，限定为"扩散模型路径不可能"

### v0.1.0 已支持（保留）
- Recraft / Gemini / Zhipu GLM / MiniMax 元件生成
- vectorizer.ai 矢量化
- Inkscape MCP 自动化排版
- PowerPoint MCP 演示输出
- 元件库目录结构与版本控制

### 已知限制
- WPS Office 无 MCP，只能通过兼容打开 PPTX
- ~~Claude 无图像生成 API，无法作为元件 provider~~ → **v0.1.1 修订**：Claude 通过 Visualizer / 直接写 SVG 代码可以作为整图 provider
- ~~完全自动化排版仍是开放问题~~ → **v0.1.1 修订**：限定为扩散路径，LLM + 元件库路径已可达 70-80%
- 中文字体处理在 Inkscape 上仍需手动指定（思源黑体等）
- LLM 写 SVG 的固有失败模式：文字坐标重叠（需 5-10 分钟 Inkscape 微调）
- BioRender 集成（订阅用户合规路径）：v0.1.1 暂不实现，等 BioRender MCP 工具暴露后另行评估

### 计划 v0.2
- 增加元件库 Web UI 检索（取代 grep _index.yaml）
- 增加 Style Reference 自动校验（每周扫描漂移）
- 增加投稿期刊配置文件（Nature/Cell/Lancet 各家的色板/字号/尺寸规范）
- **`text_collision_check.py`**：自动检测 LLM 输出 SVG 中 `<text>` bbox 重叠并提示修复
- **BioRender connector 集成**（个人订阅合规路径）
- **元件库 web preview**：基于 cmd_preview，前端浏览器可点击下载
- **低保真描摹层**（low-fidelity tracing layer）：对每一个 CC0 / AI 生成的 SVG 元件先经过 Inkscape / Adobe Illustrator 的低保真 trace 步骤，让风格异质的多源元件在嵌入模板前**先收敛到同一笔触/色板/抽象层级**，再用 Inkscape / PPT 排版输出 PDF / SVG-内嵌-SVG。这是和本版 ID 解析与 attribution 注入正交的独立步骤，单独排期，不进 v0.1.1。

### 设计原则（不会改变）
1. **80% 自动 + 20% 人决策** —— 视觉层级永远人定
2. **元件库优先** —— 重复使用 > 重新生成
3. **CC0 优先** —— 不分发受版权保护的素材
4. **可追溯性强制** —— 每张图必须有 elements-used.yaml + attribution 清单
5. **Git 版本化** —— 元件库本身是可分享的资产
