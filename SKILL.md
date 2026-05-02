---
name: scientific-illustration-asset-pipeline
version: 0.1.0
description: |
  Master skill for scientific figure creation via AI-generated element library
  plus semi-automated assembly. Coordinates multiple AI image providers
  (Recraft, OpenAI gpt-image, Gemini Imagen, Zhipu GLM CogView, MiniMax) for
  element generation, manages a versioned local asset library, and automates
  layout via Inkscape MCP and PowerPoint MCP. Triggers: "做 Fig1", "graphical
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

## 总览：从碎片化到工程化

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

### 1.1 按元件类型选 provider

| 元件类型 | 首选 | 备选 | 避免 |
|---|---|---|---|
| 抽象生物元件（细胞、蛋白）| **Recraft V3** (vector) | Gemini Imagen 4 | OpenAI DALL-E |
| 写实解剖结构（器官、组织）| **Gemini Imagen 4** | OpenAI gpt-image | Recraft (太抽象) |
| 中文标签 / 中文场景 | **智谱 CogView-4** | MiniMax | Recraft (中文糊) |
| 实验装置（仪器、培养皿）| **Recraft V3** | Gemini Imagen 4 | - |
| 信号通路单点（受体激活等）| **Recraft V3** | - | - |
| 流程图节点（圆角矩形等）| **不要用 AI** | 直接用 Inkscape/PPT 画 | 任何 AI |
| 手绘风/插画风 | OpenAI gpt-image | Gemini | Recraft (太程式化) |

### 1.2 按输出格式区分

| Provider | 直出 SVG | 直出 PNG | 中文 | 一致性 (style ref) |
|---|---|---|---|---|
| Recraft V3 | ✅ | ✅ | 弱 | ✅ 强 |
| OpenAI gpt-image | ❌ | ✅ | 中 | ❌ 无 style API |
| Gemini Imagen 4 | ❌ | ✅ | 中 | ⚠️ 通过 reference image |
| 智谱 GLM CogView-4 | ❌ | ✅ | **强** | ❌ |
| MiniMax | ❌ | ✅ | **强** | ❌ |

**关键推论**：除了 Recraft 直出 SVG，其他全部需要 PNG → SVG 矢量化步骤。
矢量化质量取决于元件复杂度——单元件简单背景，矢量化效果可接受。

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

非 Recraft 来源的元件必须经此步骤。

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

**适用**：低质量需求场景（讲座 PPT 配图）。
**严禁用于**：投稿 figure。**架构上不可能产出审稿过线的质量**。

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

### 当前版本（v0.1.0）已支持
- Recraft / Gemini / Zhipu GLM / MiniMax 元件生成
- vectorizer.ai 矢量化
- Inkscape MCP 自动化排版
- PowerPoint MCP 演示输出
- 元件库目录结构与版本控制

### 已知限制
- WPS Office 无 MCP，只能通过兼容打开 PPTX
- Claude 无图像生成 API，无法作为元件 provider
- 完全自动化排版仍是开放问题（设计本质决定）
- 中文字体处理在 Inkscape 上仍需手动指定（思源黑体等）

### 计划 v0.2
- 增加元件库 Web UI 检索（取代 grep _index.yaml）
- 增加 Style Reference 自动校验（每周扫描漂移）
- 增加投稿期刊配置文件（Nature/Cell/Lancet 各家的色板/字号/尺寸规范）

### 设计原则（不会改变）
1. **80% 自动 + 20% 人决策** —— 视觉层级永远人定
2. **元件库优先** —— 重复使用 > 重新生成
3. **可追溯性强制** —— 每张图必须有 elements-used.yaml
4. **Git 版本化** —— 元件库本身是可分享的资产
