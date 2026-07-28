---
name: "parse-prd"
description: "解析 PRD 文档并通过四阶段流水线（抽取→匹配+图搜索→推理→融合）生成增强版 PRD。当用户提到「完善 PRD」「分析需求文档」「根据本体知识补全需求」「让 PRD 更完善」「PRD 增强」「补充隐含依赖/约束/影响」时必须调用本 skill。"
---

# 解析并增强 PRD（四阶段流水线）

通过 MCP 工具 `parse_prd` 把用户提供的 PRD 文本送入四阶段流水线，借助本体知识（Ontology）推理出隐含的依赖、约束、影响，并把推理结果**融合到 PRD 原文**中，而非简单追加附录。

## 触发条件

满足以下任一情况时，必须调用 `parse_prd`：

- 用户提供一段 PRD 文本或 Markdown，并希望根据已有本体知识进行增强
- 用户说"帮我完善这份 PRD"、"分析这个需求文档"、"补充隐含的依赖/约束/影响"、"PRD 增强"
- 用户希望对一份简短需求（例如一两句话）做"展开 + 知识补全"

## MCP 工具

- 工具名：`parse_prd`
- 参数：
  - `content`（必填）：PRD 的 Markdown 或纯文本内容
  - `db_path`（可选）：数据库路径，通常不传

## 用法示例

```json
{
  "name": "parse_prd",
  "arguments": {
    "content": "修改ccb，为其添加linux端的屏幕操控能力"
  }
}
```

完整 Markdown PRD 也可以：

```json
{
  "name": "parse_prd",
  "arguments": {
    "content": "# PRD：CCB 项目增加 Linux 上的屏幕操控能力\n\n## 背景\nCCB 目前已实现 Computer Use 屏幕操控功能，但仅支持 macOS 和 Windows...\n\n## 功能需求\n### 1. Linux 屏幕截图\n...\n"
  }
}
```

## 返回值

```json
{
  "enriched_prd": "修改ccb，为其添加linux端的屏幕操控能力（隐含依赖：...；约束：...；影响：...）",
  "summary": {
    "entities_extracted": 1,
    "entities_matched": 1,
    "relations_found": 3,
    "inferences": {"dependencies": 1, "constraints": 2, "impacts": 2},
    "enhancement_mode": "fused",
    "pipeline_stages": ["extract", "match", "reason", "fuse"]
  },
  "pipeline_trace": {
    "stage1_entities": [...],
    "stage2_matches": [...],
    "stage2_subgraph": {"entities": [...], "relations": [...]},
    "stage3_inferences": {"dependencies": [...], "constraints": [...], "impacts": [...]}
  }
}
```

| 字段 | 含义 |
|------|------|
| `enriched_prd` | 增强后的 PRD Markdown，推理结果已**融合**到正文相关位置（不是简单追加附录） |
| `summary` | 解析摘要：抽取数、匹配数、关系数、推理数、增强模式、流水线阶段 |
| `pipeline_trace.stage1_entities` | 阶段1 抽取出的 PRD 实体（name/type/description/search_keywords） |
| `pipeline_trace.stage2_matches` | 阶段2a 候选预筛 + 阶段2b LLM 匹配结果 |
| `pipeline_trace.stage2_subgraph` | 阶段2c 图搜索得到的本体子图（实体 + 关系，含 1 跳和传递） |
| `pipeline_trace.stage3_inferences` | 阶段3 三 subagent 推理出的依赖/约束/影响 |

## 四阶段流水线

本工具采用**四阶段流水线**，共 **6 次 LLM 调用**（抽取 1 + 匹配 1 + 推理 3 并行 + 融合 1）：

```
PRD 原文
   │
   ▼
[1] LLM 实体抽取 ────────────────► prd_entities[]
   │                                ↓
   │                          search_keywords 做 SQL LIKE 预筛
   ▼                                ↓
[2] 语义匹配 + 图搜索  ──► LLM 批量语义匹配 ──► 匹配实体
                              │              ↓
                              │         1 跳 + 传递 BFS
                              ▼              ↓
                            matched subgraph{entities, relations}
                              │              ↓
                              ▼              ↓
[3] LLM 推理（3 subagent 并行） ──► dependencies/constraints/impacts
                              │
                              ▼
[4] LLM 融合 ──► enriched_prd（融合回原文）
```

### 阶段 1：LLM 实体抽取
- **输入**：PRD 原文
- **LLM 任务**：抽取功能级实体
- **输出**：`[{name, type, description, search_keywords}]`
- **温度**：0.1（来自 `llm_config.yaml` 的 `prd_parser.temperature`）

### 阶段 2：语义匹配 + 图搜索
本阶段拆为三步，**只做一次 LLM 调用**（用于语义匹配）：

- **Step 2a：SQL LIKE 预筛选**
  - 对每个 PRD 实体的 `search_keywords` 在 `entities` 表做 `name LIKE` / `description LIKE` 查询
  - 每个关键词取 top 5 候选，去重后作为匹配候选
- **Step 2b：LLM 批量语义匹配**
  - 输入：所有 PRD 实体 + 其候选列表
  - 输出：`[{prd_entity_name, matched_entity_id, confidence, match}]`
  - 阈值：`prd_parser.extraction.entity_confidence_threshold`（默认 0.5）
- **Step 2c：图搜索（1 跳 + 多跳 BFS）**
  - 对每个匹配到的实体做 1 跳关系查询（`get_entity_relations`，全部类型）
  - 对 `depends_on` / `contains` / `derived_from` 三种可传递关系做 BFS（`get_transitive_relations`，`max_depth=2`）
  - 收集 1 跳 + 传递关系两端的实体，构建子图

### 阶段 3：LLM 推理（3 个并行 subagent）
三个 subagent **并行**执行，分别从不同角度推理：

| Subagent | 角色 | 推理目标 | LLM 温度 |
|----------|------|---------|---------|
| A | 依赖推理 | PRD 中隐含的依赖（子图未直接显示但可推断） | 0.1 |
| B | 约束推理 | PRD 中隐含的约束条件 | 0.1 |
| C | 影响推理 | 此变更会影响到哪些已有模块 | 0.1 |

每个 subagent 的输入是 **PRD 原文 + 子图文本**，输出是 `inferences: [...]`。

### 阶段 4：LLM 融合
- **输入**：PRD 原文 + 推理结果 JSON + 子图摘要
- **LLM 任务**：把依赖/约束/影响**自然融合**到 PRD 原文相关位置
- **温度**：0.7（来自 `prd_parser.prd_generator.temperature`，更具创造性）
- **最大 tokens**：8192

## 增强模式说明

增强模式 = `fused`（深度融合），区别于旧版的"括号标注"模式：

| 旧模式（已废弃） | 新模式（fused） |
|---------------|---------------|
| 在原文后追加一个"## 关系分析"附录 | 推理结果直接融入原文相关位置 |
| 信息与正文分离 | 读起来像一份更完善的需求文档 |
| 容易遗漏上下文 | 推理发生在原段落中 |

**融合规则示例**：
- 原文"在 Linux 上实现屏幕截图功能"
- 融合后"在 Linux 上实现屏幕截图功能（**隐含依赖**：复用 CCB Computer Use 模块的跨平台截图基础设施）"

## 端到端测试用例

输入：`"修改ccb，为其添加linux端的屏幕操控能力"`

实测中间结果（来自 `test_e2e_ccb_linux.py`）：

| 阶段 | 输出 | 耗时 |
|------|------|------|
| 1 抽取 | 1 个实体 `Linux端屏幕操控能力`，含 5 个 search_keywords | ~15s |
| 2 匹配 | 匹配到 `ccb:func:screen_control`（conf 0.85） | ~11s |
| 2 子图 | 3 实体（`screen_control` / `Bash 执行` / `Computer Use`） + 3 关系 | ~11s |
| 3 推理 | 1 依赖 + 2 约束 + 2 影响 | ~20s |
| 4 融合 | 一段融合后的自然语言需求 | ~15s |
| **总耗时** | | **~61s** |

## 前置条件

调用本工具前，确保：

1. **本体数据库非空**：`ontology.db` 中至少存在与 PRD 相关的实体和关系，否则阶段 2 会匹配失败
2. **LLM 配置正确**：`llm_config.yaml` 中 `default_provider` / `api_key` / `base_url` / `model` 正确
3. **依赖工具就绪**：`models/entity.py` 中的 `search_entities`、`get_entities_by_ids` 和 `models/relation.py` 中的 `get_entity_relations`、`get_transitive_relations` 可用

## 与其他 skill 的协作

| 上游 skill | 用途 |
|-----------|------|
| `prd-ontology-seeder` | 把领域文档写入本体，构建 `ontology.db` 中的实体和关系 |
| `ingest-document` | 把零散文本/说明加入本体 |
| `query-ontology` | 查询本体中的实体和关系（可用于诊断） |

| 下游 skill | 用途 |
|-----------|------|
| `prd-writer` | 拿到 `enriched_prd` 后做人工润色/改写为面向终端用户的产品文档 |

## 注意事项

- **只读分析**：本工具**不会**修改本体数据库
- **配置可热更新**：`llm_config.yaml` 每次调用都重新读取，无需重启服务
- **失败重试**：每个 LLM 调用有 `max_retries=3` 的指数退避重试（1s/2s/4s）
- **性能瓶颈**：阶段 3 三个 subagent 串行总耗时≈单个 subagent 耗时 × 1.1（线程池开销）
- **可观测性**：`pipeline_trace` 字段直接用于调试和观测各阶段中间结果
- **匹配阈值可调**：在 `llm_config.yaml` 的 `prd_parser.extraction.entity_confidence_threshold` 中调整（默认 0.5）
