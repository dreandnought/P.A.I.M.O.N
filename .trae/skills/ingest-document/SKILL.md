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

## 注意事项

- 再次执行时建议同时传入原始 `content`，否则文档溯源记录的 content_hash/word_count 可能为空
- 实体按名称去重，关系按 source/target/type 去重
