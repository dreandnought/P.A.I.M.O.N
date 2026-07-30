# PRD Ontology MCP 开发文档

## 项目概述

为 Coding Agent（Claude Code、Cline 等）构建 MCP 插件，增强其对 PRD 的理解能力。将 PRD 中的实体和隐性关系建模为显式 Ontology 图，让 Agent 不仅能"读"PRD，更能"理解"其中的因果链、约束关系、依赖网络。

## 验证阶段

当前处于**验证阶段**，目标是尽快构建轻量级原型跑通方案。

### 关键原则

- **不引入向量模型**（无 embedding，纯 LLM 语义匹配）
- **SQLite 存储**（零依赖，轻量）
- **可读实体 ID**（便于调试和人工检查）
- **LLM 只抽取第一层实体**，关系从预设 Ontology 匹配
- **预设 Ontology 由 Agent 按结构化协议构建**（详见 ONTOLOGY_BUILD_GUIDE.md）

### MVP 范围

只实现 `parse_prd` + `query_ontology` 两个 MCP 工具，暂不做冲突检测和影响分析。

### 核心流程

```
Phase 1（离线/预处理 - 由 Agent 按指南构建）
  企业知识（代码库、API文档、历史PRD、Wiki等）
    → 按 ONTOLOGY_BUILD_GUIDE.md 协议
    → 构建预设 Ontology（SQLite 数据库文件）
    ↓
Phase 2（在线/每次 PRD 解析）
  新 PRD 到达
    → parse_prd 调用 LLM 抽取第一层实体（功能级）
    → 在预设 Ontology 中匹配实体（LLM 语义匹配）
    → 查询匹配到的实体的关联关系
    → 关系以 Markdown 附录追加到 PRD 末尾
    → 返回增强版 PRD → Coding Agent 做任务规划
```

### 已确认决策

| 项目 | 决策 |
|------|------|
| 阶段 | 验证阶段，不引入向量模型 |
| PRD 输入格式 | Markdown/纯文本，保留结构化 |
| LLM 抽取粒度 | 功能级实体（功能、模块、接口） |
| 实体匹配方案 | 纯 LLM 语义匹配（MVP），后续升级 FTS5+LLM |
| 匹配时机 | 在 parse_prd 同一个 LLM prompt 中完成 |
| 输出格式 | Markdown 附录追加到 PRD 末尾 |
| 返回方式 | 同步返回增强版 PRD |
| 预设 Ontology 存储 | SQLite 数据库（按协议由 Agent 构建） |
| 实体 ID 格式 | `{type_short}:{normalized_name}`，如 `func:user_login` |
| 关系类型 | depends_on, causes, constrains, impacts, conflicts_with, derived_from, implements, contains, refines, relates_to |
| 实体类型 | requirement, function, module, interface, data_entity, test_case, constraint, actor |

## 技术栈

- **运行时**：Python + FastMCP
- **存储**：SQLite（轻量级，零依赖）
- **LLM**：DeepSeek V4 API（或本地 Qwen2.5-7B 量化版）
- **传输**：stdio（MCP 标准）

## MCP 工具接口设计

### 1. parse_prd

**作用：** 解析 PRD 文档，抽取第一层实体，在预设 Ontology 中匹配并查询关系，返回增强版 PRD。

**输入：**
```json
{
  "content": "string (PRD 的 Markdown/纯文本内容)"
}
```

**输出：**
```json
{
  "enriched_prd": "string (增强后的 PRD Markdown，末尾追加了关系附录)",
  "summary": {
    "entities_extracted": 5,
    "entities_matched": 3,
    "relations_found": 8,
    "new_entities_added": 2
  }
}
```

**处理流程：**
1. LLM 从 PRD 中抽取第一层实体（功能级）
2. LLM 将实体映射到预设 Ontology 中的实体
3. 查询匹配实体的关联关系（SQLite 查询）
4. LLM 将关系整理为 Markdown 附录
5. 返回增强版 PRD

**关系附录格式示例：**
```markdown
## 🔗 需求关系图谱（自动分析）

### 依赖关系
- 用户登录 → 依赖 → 短信验证码服务
- 订单提交 → 依赖 → 库存查询接口

### 约束关系
- 支付必须在订单确认之后
- 密码长度 8-32 位

### 影响关系
- 修改用户认证模块 → 影响 → 登录、注册、找回密码
```

---

### 2. query_ontology

**作用：** 查询 Ontology 中的实体和关系。供 Coding Agent 在后续任务规划中按需查询更多信息。

**输入（方案 A - 按实体名称查询）：**
```json
{
  "entity_name": "string (实体名称)",
  "relation_types": ["depends_on", "impacts"] (可选，筛选关系类型)
}
```

**输入（方案 B - 按实体 ID 查询）：**
```json
{
  "entity_id": "string (实体 ID)",
  "relation_types": ["depends_on", "impacts"] (可选，筛选关系类型)
}
```

**输入（方案 C - 搜索）：**
```json
{
  "query": "string (关键词搜索)",
  "limit": 10 (可选，默认 10)
}
```

**统一输出：**
```json
{
  "entity": {
    "id": "func:user_login",
    "name": "用户登录",
    "type": "function",
    "description": "用户登录功能，支持手机号和邮箱登录",
    "properties": {}
  },
  "relations": [
    {
      "type": "depends_on",
      "direction": "outgoing",
      "target_entity": {
        "id": "iface:sms_service",
        "name": "短信验证码服务",
        "type": "interface"
      },
      "confidence": 0.95,
      "reason": "登录需要验证手机号"
    },
    {
      "type": "impacts",
      "direction": "outgoing",
      "target_entity": {
        "id": "mod:auth_module",
        "name": "用户认证模块",
        "type": "module"
      },
      "confidence": 0.85,
      "reason": "登录功能属于认证模块"
    }
  ]
}
```

**说明：** 方案 A 和 B 任选其一传入。如果都传，优先使用 `entity_id`。方案 C 用于关键词搜索（匹配实体名称和描述）。

## 预设 Ontology 初始化

### 数据源
- 历史 PRD 文档
- 代码库（模块结构、函数、接口依赖）
- API 文档 / Swagger / OpenAPI
- 团队 Wiki / 设计文档
- Bug 记录、Changelog

### 初始化流程
1. 从代码库提取模块结构和接口定义（静态分析）
2. 从 API 文档提取接口实体和关系
3. 从历史 PRD 用 LLM 抽取实体和关系
4. 全局实体消歧
5. 存入 SQLite

### 协议文档
详见 `ONTOLOGY_BUILD_GUIDE.md`，该文档定义了：
- 实体 ID 命名规范
- SQLite 表结构和字段协议
- 实体类型和关系类型枚举
- 关系方向性定义（对称/非对称）
- 置信度和元数据字段规范

## 工程结构

```
prd-ontology-mcp/
├── server.py                 # MCP Server 入口
├── requirements.txt          # 依赖
├── DEVELOPMENT.md            # 开发文档（本文件）
├── ONTOLOGY_BUILD_GUIDE.md   # Ontology 构建指南（供其他 Agent 使用）
├── models/
│   ├── __init__.py
│   ├── schema.py             # SQLite 表定义 + 初始化
│   ├── entity.py             # Entity CRUD
│   └── relation.py           # Relation CRUD
├── parser/
│   ├── __init__.py
│   └── llm_parser.py         # LLM 驱动的 PRD 解析
├── tools/
│   ├── __init__.py
│   ├── parse_prd.py          # MCP Tool: parse_prd
│   └── query_ontology.py     # MCP Tool: query_ontology
├── storage/
│   ├── __init__.py
│   └── connection.py         # SQLite 连接管理
└── init/
    ├── __init__.py
    └── seed.py               # 预设 Ontology 初始化（按协议）
```

## 开发进度记录

### Phase 0: MVP 阶段（已完成 ✅）

- **时间**：2026-07-07 ~ 2026-07-14
- **内容**：
  - SQLite 存储层（entities + relations + entity_types + relation_types + documents）
  - Entity / Relation CRUD（含版本历史 Event Sourcing）
  - LLM 解析器（四阶段流水线：抽取 -> 匹配+图搜索 -> LLM 推理 -> 融合）
  - 5 个 MCP 工具（parse_prd / query_ontology / ingest_document / modify_ontology / delete_entity）
  - Web 可视化前端
  - 4 个 Trae Skill 定义
  - 测试数据（CCB 73 实体 + Anthropic 40 实体）

### Phase 1: 规则推理引擎（已完成 ✅）

- **时间**：2026-07-23
- **内容**：
  - 设计并实现了基于规则的本体推理引擎，替代 LLM 语义猜测
  - LLM 调用从 6 次降至 2 次（抽取+匹配 / PRD 融合）
  - 推理结果完全可复现，每条推理有规则名称和明确证据
  - 7 大推理规则：
    1. **传递闭包**（transitive closure）- 递归 CTE 计算多跳间接依赖
    2. **对称推理**（symmetric inference）- 对称关系自动推导反向
    3. **逆关系推理**（inverse relation）- 正向关系的逆向语义
    4. **约束传播**（constraint propagation）- 约束沿包含关系传播给子实体
    5. **影响分析**（impact analysis）- BFS 遍历正/反/双向关系网络
    6. **类型继承**（type inheritance）- 兄弟实体共性关系推导
    7. **冲突检测**（conflict detection）- 4 种矛盾模式检测
  - 一致性检查器（类型兼容性 / 孤立实体 / 推理矛盾）
  - 8 个单元测试全部通过
  - 新增 `parse_prd_v2()` 函数，保留原 `parse_prd()` 向后兼容
  - `tools/parse_prd.py` 已切换到 V2 流水线

### Phase 2: 端到端集成测试（已完成 ✅）

- **时间**：2026-07-28
- **内容**：
  - 用真实 PRD（用户登录功能需求）完整走通 `parse_prd_v2` 流水线
  - 创建测试本体数据库：13 个实体 + 19 条关系
  - LLM 抽取 14 个实体 → 8 个匹配到本体 → 规则引擎产出 **67 条推理结果** → LLM 融合
  - PRD 长度：276 → **5,438 字符**（增强 5,162 字符）
  - LLM 调用：6 次 → **2 次**（-67%）
  - 推理可复现：规则引擎确定性推理，每条有 `rule_name` + `evidence`

### Phase 3: Schema 层增广（已完成 ✅）

- **时间**：2026-07-28
- **内容**：
  - `relation_types` 表新增 `inverse_of`, `domain_type`, `range_type` 字段
  - 预置类型层次：function→requirement, interface→module, data_entity→actor, constraint→requirement, test_case→requirement
  - 关系语义修正：causes transitive=1, 5 种 domain/range 约束
  - 新增 `models/schema_manager.py` — Schema 管理器
  - 新增 `tools/manage_schema.py` — MCP 工具
  - 增广 `parser/llm_parser.py` — LLM prompt 支持 Schema 分析

### Phase 4: 推理引擎增强与工程化（已完成 ✅）

- **时间**：2026-07-30
- **内容**：
  - **新增 `reason_ontology` MCP 工具** - 直接暴露推理引擎给 Agent
    - 支持 3 种实体输入方式（ID / 名称 / 关键词搜索）
    - 支持按规则筛选（`rules_only` 参数）
    - 支持按推理类型筛选（`inference_type` 参数）
    - 返回 `llm_summary` 可直接用于 Agent 上下文
  - **增强 `query_ontology` 工具** - 新增推理查询能力
    - `include_inferences=true` 时附加推理结果
    - `inference_rules` 参数指定推理规则子集
    - 返回 `inference_summary` 可读摘要
  - **推理结果缓存** - `engine/cache.py`
    - 内存级缓存，基于实体 IDs 哈希 + 规则配置
    - 实体/关系变更时自动失效相关缓存
    - LRU 淘汰策略 + TTL 过期
    - 缓存命中率统计
  - **一致性检查器增强** - 新增 2 项检查
    - 循环依赖检测（递归 CTE 检测 A->B->...->A）
    - 置信度异常检测（过低置信度 + 深度过深但高置信度）
    - 扩展不兼容类型组合
    - 新增 implements + conflicts_with 矛盾检测
  - **自监督反馈工作流** - `engine/feedback.py` + `tools/manage_feedback.py`
    - `feedback_log` 表自动记录每次 `parse_prd_v2` 的预测
    - `manage_feedback` MCP 工具（submit / stats / list）
    - `prediction_id` 串联预测与验证结果
    - 反馈统计（准确率 / 待验证数 / 按来源分组）
  - **Web 看板增强** - 新增 5 个 API 端点
    - `POST /api/reasoning/run` - 在线运行推理
    - `GET /api/cache/stats` - 缓存统计
    - `POST /api/cache/clear` - 清除缓存
    - `GET /api/feedback/stats` - 反馈统计
    - `GET /api/feedback/list` - 反馈列表
    - `POST /api/feedback/submit` - 提交反馈
  - **16 个单元测试全部通过**（8 原有 + 8 Phase 4 新增）

### Phase 5: 待开发

- [ ] Web 看板前端页面完善（推理结果可视化展示）
- [ ] 自监督学习自动参数调优（基于反馈数据校准规则参数）
- [ ] 推理规则可配置化（YAML 配置规则参数）
- [ ] 多项目本体联邦
- [ ] 代码一致性检查工具（`check_consistency` MCP 工具）

## 自监督迭代框架（Phase 2+）

不在 MVP 范围内，详见 wiki 方案文档 `wiki/ideas/2026-07-07-prd-ontology-mcp-plugin.md`。

## 相关文档

- [PRD Ontology MCP 插件方案（wiki）](../wiki/ideas/2026-07-07-prd-ontology-mcp-plugin.md)
- [Palantir Ontology 深度调研](../wiki/tech/ai-coding/2026-07-08-palantir-ontology-deep-dive.md)
- [类似 Palantir 的开源代码分析项目调研](../wiki/tech/ai-coding/2026-07-06-ontology-like-code-analysis-projects.md)
- [推理引擎开发计划](REASONING_ENGINE_DEV_PLAN.md)

## 更新历史

| 日期 | 更新内容 |
|------|----------|
| 2026-07-07 | 项目创建，MVP 方案设计 |
| 2026-07-14 | MVP 阶段完成，四阶段流水线 + 5 个 MCP 工具 |
| 2026-07-23 | 规则推理引擎完成，LLM 调用从 6 次降至 2 次，7 大规则 + 一致性检查 |
| 2026-07-24 | SQLite 存储层 + MCP 服务器完成 |
| 2026-07-28 | 端到端集成测试通过（PRD 276→5438 字符，67 条推理）| Schema 层增广完成（类型层次 + 关系语义 + manage_schema 工具）| 清理历史仓库 PRDOntology/ + prd-ontology-mcp/，统一到 CodingOntology |
| 2026-07-30 | Phase 4 完成：reason_ontology 工具 + query_ontology 推理增强 + 缓存 + 一致性检查增强 + 自监督反馈工作流 + Web API 增强 |
