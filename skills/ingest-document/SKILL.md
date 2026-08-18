---
name: "ingest-document"
description: "从文本或 Markdown 中抽取实体和关系并更新本体。当用户输入一段文字/文档并要求写入本体时调用。"
---

# 摄入文档并更新本体

通过 MCP 工具 `ingest_document` 从文本或 Markdown 文档中自动抽取实体和关系，并写入本体数据库。

## 触发条件

- 用户提供一段描述、说明或 Markdown 文档
- 用户希望把其中的知识点沉淀到本体库
- 用户说“把这段文字加入本体”或“从这段内容提取关系”

## MCP 工具

- 工具名：`ingest_document`
- 参数：
  - `content`（必填）：文本或 Markdown 内容
  - `title`（可选）：文档标题，用于溯源，默认“未命名文档”
  - `dry_run`（可选）：默认 `true`，只返回变更计划不写入；设为 `false` 执行写入
  - `plan`（可选）：传入 dry_run 返回的计划，避免 LLM 两次调用结果不一致
  - `db_path`（可选）：数据库路径，通常不传

## 推荐调用流程

1. 先以 `dry_run=true` 调用，查看变更计划
2. 确认计划合理后，将返回的 `plan` 以 `dry_run=false` 再次调用执行写入

## 用法示例

dry_run 阶段：

```json
{
  "name": "ingest_document",
  "arguments": {
    "content": "Gateway 模块依赖配置中心，并通过 Redis 缓存路由规则。",
    "title": "架构说明",
    "dry_run": true
  }
}
```

执行写入阶段（使用返回的 plan）：

```json
{
  "name": "ingest_document",
  "arguments": {
    "title": "架构说明",
    "dry_run": false,
    "plan": { /* 上一步返回的 plan */ }
  }
}
```

## 返回值

- dry_run 时：返回 `plan` 和 `extraction_summary`
- 执行写入时：返回 `document`、`created_entities`、`updated_entities`、`created_relations`

## ⚠️ 关系类型白名单（重要约束）

**禁止创建实体关系类型之外的新关系类型。**

抽取关系时，`relation_type` 只能从以下 **10 种预设关系类型**中选择语义最相近的一种：

| 类型 | 含义 | 示例 |
|------|------|------|
| `depends_on` | A 依赖 B 才能正常工作 | 用户登录 → 依赖 → 短信验证码服务 |
| `causes` | A 的发生会导致 B | 库存超卖 → 导致 → 订单失败 |
| `constrains` | A 对 B 有约束条件 | 支付 → 约束 → 订单确认之后 |
| `impacts` | 修改 A 会影响 B | 修改认证模块 → 影响 → 登录/注册 |
| `conflicts_with` | A 和 B 互斥或冲突 | 方案A → 冲突 → 方案B |
| `derived_from` | A 是从 B 派生/衍生出来的 | 风控报告 → 派生自 → 交易流水 |
| `implements` | A 实现了 B（接口/需求） | 登录模块 → 实现 → 登录需求 |
| `contains` | A 包含 B（父子关系） | 订单模块 → 包含 → 订单查询 |
| `refines` | A 是对 B 的细化/补充 | 支付流程 → 细化 → 退款规则 |
| `relates_to` | A 和 B 有关联（通用兜底） | （无更贴切类型时使用） |

**选择规则：**
1. 优先选语义最精确的类型；没有精确匹配时，选最相近的类型；
2. 仍无法匹配时，统一用 `relates_to`（通用关联）兜底；
3. **绝不捏造或发明新关系类型字符串**（如 `triggers`、`blocks`），应映射到上述 10 种；
4. `relations.create` 中出现的 `relation_type` 若不在上述 10 种之内，视为非法，应修正后再提交。

## 注意事项

- **再次执行时建议同时传入原始 `content`**，否则文档溯源记录的 content_hash/word_count 可能为空
- 实体按名称去重，关系按 source/target/type 去重
- **⚠️ `plan` 必须是嵌套 dict 对象，不能是 JSON 字符串**。MCP 返回 dry_run 结果时，`plan` 被编码在 `text` 字段的 JSON 字符串中，调用方需要先反序列化成 Python dict，再以嵌套对象形式传入 `arguments.plan`
- **`plan` 结构骨架**（必须与 dry_run 返回值保持一致）：

```json
{
  "entities": {
    "create": [
      {"entity_id": "", "name": "", "type": "", "description": "", "confidence": 0.8}
    ],
    "update": [
      {"entity_id": "", "name": "", "type": "", "description": "", "confidence": 0.8}
    ],
    "delete": []
  },
  "relations": {
    "create": [
      {"source_id": "", "target_id": "", "relation_type": "", "description": "", "confidence": 0.8}
    ],
    "update": [],
    "delete": []
  },
  "skipped_relations": []
}
```

- 若传入 `plan` 时结构错误，工具会返回 `plan_validation_error` 而不再崩溃
