# 计划：重写 parse_prd 为四阶段流水线

## 概述

将当前 `parse_prd` 的"单次 LLM 调用做全部事情"重写为四阶段流水线：
1. **LLM 实体抽取** — 从 PRD 文本中提取感兴趣实体
2. **语义匹配 + 图搜索** — 用实体在本体中做模糊匹配，再 BFS 遍历 relations 表获取子图
3. **LLM 推理** — 基于子图推理隐含的依赖、约束、影响（3 个并行 subagent）
4. **LLM 融合** — 将推理结果融合回 PRD 原文，生成更完善的增强版

## 当前状态分析

### 现有实现的问题（`parser/llm_parser.py:369-482`）

| 问题 | 详情 |
|------|------|
| 单次 LLM 调用 | `parse_prd()` 只调一次 `_call_llm`，抽取/匹配/融合全塞一个 prompt |
| 全量 dump 实体 | `_build_ontology_context()` 无条件 SELECT 全部 179 个实体到 prompt |
| relations 表未参与 | 完全不读 `relations` 表，图搜索函数 `get_transitive_relations` 从未被调用 |
| 无结构化推理 | 推理全靠 LLM 在同一轮响应里"脑补" |
| 融合质量低 | 只做括号标注（`用户登录（依赖短信验证码服务）`），非真正融合 |
| 配置未利用 | `llm_config.yaml` 的 `prd_parser`/`prd_generator` 段从未被代码读取 |

### 可复用的现有代码

| 函数 | 文件 | 用途 |
|------|------|------|
| `search_entities(query, limit)` | `models/entity.py:38-50` | SQL LIKE 预筛选候选实体 |
| `get_entity_relations(entity_id)` | `models/relation.py:8-41` | 1跳双向关系查询 |
| `get_transitive_relations(entity_id, type, max_depth)` | `models/relation.py:104-131` | 多跳 BFS（用于 depends_on/contains/derived_from） |
| `get_entities_by_ids(ids)` | `models/entity.py:65-78` | 批量查询实体详情 |
| `_call_llm(system, user, temp, max_tokens)` | `parser/llm_parser.py:54-94` | LLM API 调用 |
| `_first_json_block(text)` | `parser/llm_parser.py:125-137` | 从 LLM 响应中提取 JSON |

### 可传递关系类型（适合 BFS）

来自 `models/schema.py:104-115` 的 `DEFAULT_RELATION_TYPES`：
- `depends_on`（transitive=1）— 依赖链
- `contains`（transitive=1）— 层级包含
- `derived_from`（transitive=1）— 派生链

## 改动方案

### 文件 1：`parser/llm_parser.py`（核心重写）

#### 新增函数

**`_extract_prd_entities(content: str) -> list[dict]`** — 阶段 1

- LLM 调用，输入仅 PRD 文本（不带 ontology 上下文）
- System prompt：你是实体抽取专家，从 PRD 中抽取功能级实体
- 输出 JSON：`[{name, type, description, search_keywords: [str]}]`
- `search_keywords` 是新增字段，用于阶段 2 的 SQL LIKE 预筛选
- 使用 `prd_parser` 配置（temperature=0.1）

**`_semantic_match_entities(prd_entities, db_path) -> list[dict]`** — 阶段 2a+2b

- Step 2a：对每个 PRD 实体的 `search_keywords`，调用 `search_entities(keyword, limit=5)` 获取候选
- Step 2b：一次 LLM 调用，批量匹配所有 PRD 实体与各自的候选列表
  - System prompt：你是实体匹配专家，判断 PRD 实体与候选实体的语义相似度
  - 输出 JSON：`[{prd_entity_name, matched_entity_id, confidence, match: bool}]`
  - 置信度阈值：`prd_parser.extraction.entity_confidence_threshold`（默认 0.5）
- 返回匹配结果列表

**`_graph_search(matched_entity_ids, db_path, max_depth=2) -> dict`** — 阶段 2c

- 对每个匹配到的实体 ID：
  - 调用 `get_entity_relations(entity_id)` 获取 1 跳关系（全部类型）
  - 对可传递关系类型（`depends_on`, `contains`, `derived_from`），调用 `get_transitive_relations(entity_id, type, max_depth)` 获取多跳
- 收集子图：`{entities: [...], relations: [...]}`
- 去重（同一实体/关系可能被多个路径命中）
- 返回结构化子图

**`_reason_inferences(content, subgraph) -> dict`** — 阶段 3（3 个并行 subagent）

- 使用 `concurrent.futures.ThreadPoolExecutor` 并行调用 3 个 LLM subagent：
  - **Subagent A（依赖推理）**：基于子图，PRD 中存在哪些隐含依赖？
    - 输入：PRD 文本 + 子图
    - 输出：`{inferences: [{source_entity, dependency, evidence, confidence}]}`
  - **Subagent B（约束推理）**：基于子图，PRD 中存在哪些隐含约束？
    - 输入：PRD 文本 + 子图
    - 输出：`{inferences: [{entity, constraint, evidence, confidence}]}`
  - **Subagent C（影响推理）**：基于子图，PRD 中的变更会影响哪些模块？
    - 输入：PRD 文本 + 子图
    - 输出：`{inferences: [{entity, impacted_module, evidence, confidence}]}`
- 合并 3 个 subagent 的结果：`{dependencies: [...], constraints: [...], impacts: [...]}`
- 每个 subagent 使用 `prd_parser` 配置（temperature=0.1）

**`_fuse_prd(content, inferences, subgraph) -> str`** — 阶段 4

- LLM 调用，输入为 PRD 原文 + 推理结果 + 子图摘要
- System prompt：你是 PRD 增强专家。将推理结果**融合**到 PRD 原文中，而非简单追加。具体规则：
  - 在相关实体描述处补充隐含依赖（如"该功能隐含依赖 XXX"）
  - 在相关需求处补充约束条件（如"该需求受 XXX 约束"）
  - 在涉及变更的模块处标注影响范围（如"此变更将影响 XXX"）
  - 保留原文全部结构和内容，在合适位置插入增强信息
  - 不要在末尾追加附录
- 使用 `prd_generator` 配置（temperature=0.7, max_tokens=8192）
- 返回增强后的 Markdown 文本

**`_load_prd_parser_config() -> dict`** — 配置加载

- 从 `llm_config.yaml` 的 `prd_parser` 段读取配置
- 返回：`{temperature, max_retries, entity_confidence_threshold, relation_confidence_threshold}`
- 用于阶段 1-3

**`_load_prd_generator_config() -> dict`** — 配置加载

- 从 `llm_config.yaml` 的 `prd_generator` 段读取配置
- 返回：`{temperature, max_tokens}`
- 用于阶段 4

**`_call_llm_with_retry(system_prompt, user_prompt, config) -> str`** — 带重试的 LLM 调用

- 包装 `_call_llm`，增加 `max_retries` 次重试
- 指数退避（1s, 2s, 4s）

#### 重写函数

**`parse_prd(content, db_path) -> dict`** — 流水线编排

```python
def parse_prd(content: str, db_path=None) -> dict:
    # 阶段 1：LLM 实体抽取
    prd_entities = _extract_prd_entities(content)

    # 阶段 2：语义匹配 + 图搜索
    match_results = _semantic_match_entities(prd_entities, db_path)
    matched_ids = [m["matched_entity_id"] for m in match_results if m["match"]]
    subgraph = _graph_search(matched_ids, db_path, max_depth=2)

    # 阶段 3：LLM 推理（3 个并行 subagent）
    inferences = _reason_inferences(content, subgraph)

    # 阶段 4：LLM 融合
    enriched_prd = _fuse_prd(content, inferences, subgraph)

    return {
        "enriched_prd": enriched_prd,
        "summary": {
            "entities_extracted": len(prd_entities),
            "entities_matched": len(matched_ids),
            "relations_found": len(subgraph["relations"]),
            "inferences": {
                "dependencies": len(inferences["dependencies"]),
                "constraints": len(inferences["constraints"]),
                "impacts": len(inferences["impacts"]),
            },
            "enhancement_mode": "fused",
            "pipeline_stages": ["extract", "match", "reason", "fuse"],
        },
        "pipeline_trace": {
            "stage1_entities": prd_entities,
            "stage2_matches": match_results,
            "stage2_subgraph": subgraph,
            "stage3_inferences": inferences,
        },
    }
```

#### 保留不变的函数

- `_load_llm_config()` — 保留，供 `_call_llm` 底层使用
- `_chat_url()` — 保留
- `_call_llm()` — 保留，被 `_call_llm_with_retry` 包装
- `_build_ontology_context()` — 保留但**不再被 `parse_prd` 调用**（其他函数如 `extract_entities_and_relations` 仍在用）
- `_extract_json_blocks()` / `_first_json_block()` — 保留
- `extract_entities_and_relations()` — 保留（`ingest_document` 工具在用）
- `plan_ontology_changes()` — 保留（`modify_ontology` 工具在用）

### 文件 2：`tools/parse_prd.py`（更新文档）

- 更新 `parse_prd` 工具的 docstring，说明四阶段流水线
- 返回值说明增加 `pipeline_trace` 字段

### 文件 3：`skills/parse-prd/SKILL.md`（更新描述）

- 更新"增强模式说明"段，描述四阶段流水线
- 更新返回值说明
- 新增"流水线阶段"说明段

### 文件 4：`test_parse_prd_enhanced.py`（更新测试）

- 更新测试输出，显示四阶段流水线的中间结果
- 打印 `pipeline_trace` 中的各阶段输出

## 假设与决策

| 决策 | 理由 |
|------|------|
| 语义匹配用 SQL LIKE 预筛选 + LLM 批量确认 | 项目无 embedding 模型；LIKE 预筛选将候选从 179 个缩窄到每个实体 ~5 个，再让 LLM 做语义判断 |
| 图搜索深度默认 2 跳 | 1 跳太浅（看不到传递依赖），5 跳太深（噪声多）；2 跳覆盖大多数实际依赖链 |
| 推理用 3 个并行 LLM subagent | 用户明确要求"subagent 参与"；3 个维度（依赖/约束/影响）独立推理，用 `ThreadPoolExecutor` 并行 |
| 融合用独立 LLM 调用（temp=0.7） | 融合是创造性任务，需要更高温度；`prd_generator` 配置段已预留 |
| 保留 `_build_ontology_context` | `extract_entities_and_relations()` 仍在用它（`ingest_document` 工具依赖） |
| `pipeline_trace` 作为可选返回字段 | 便于调试和观测中间结果，不影响 `enriched_prd` 的使用 |
| 每阶段失败时优雅降级 | 阶段 2 失败 → 返回原 PRD + 警告；阶段 3 失败 → 用子图直接做融合；阶段 4 失败 → 返回原 PRD + 结构化推理结果 |

## 验证步骤

1. **单元验证**：运行 `python3 test_parse_prd_enhanced.py`，确认：
   - 四阶段全部执行
   - `summary.pipeline_stages == ["extract", "match", "reason", "fuse"]`
   - `enriched_prd` 中包含推理得到的隐含依赖/约束/影响信息
   - `pipeline_trace` 中各阶段有结构化输出

2. **回归验证**：运行 `python3 test_parse_prd.py`，确认旧测试不报错

3. **MCP 工具验证**：通过 `test_mcp_stdio.py` 调用 `parse_prd`，确认 MCP 接口正常

4. **对比验证**：用同一份 PRD 分别跑旧版（git stash 后）和新版，对比 `enriched_prd` 质量：
   - 旧版：只有括号标注
   - 新版：在相关段落中融入了隐含依赖、约束、影响信息
