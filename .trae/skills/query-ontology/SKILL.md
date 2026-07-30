---
name: "query-ontology"
description: "查询本体中的实体及其关系。当用户想查找某个实体、查看关联关系或搜索本体知识库时调用。"
---

# 查询本体

通过 MCP 工具 `query_ontology` 查询本体（Ontology）中的实体和关系。

## 触发条件

- 用户问“xxx 和 yyy 有什么关系”
- 用户想查询某个实体/功能/模块/接口在本体中的定义和关联
- 用户需要搜索本体知识库

## MCP 工具

- 工具名：`query_ontology`
- 参数：
  - `entity_id`（可选）：按实体 ID 查询，优先级最高
  - `entity_name`（可选）：按实体名称查询
  - `query`（可选）：关键词搜索，支持名称和描述匹配
  - `relation_types`（可选）：筛选关系类型列表，例如 `["depends_on", "impacts"]`
  - `limit`（可选）：搜索结果条数，默认 10
  - `db_path`（可选）：数据库路径，通常不传

## 用法示例

查询名为 "Gateway" 的实体及其关系：

```json
{
  "name": "query_ontology",
  "arguments": {
    "entity_name": "Gateway"
  }
}
```

按 ID 查询：

```json
{
  "name": "query_ontology",
  "arguments": {
    "entity_id": "mod:gateway"
  }
}
```

关键词搜索：

```json
{
  "name": "query_ontology",
  "arguments": {
    "query": "用户登录",
    "limit": 5
  }
}
```

## 返回值

- `entity`：匹配到的实体信息
- `relations`：该实体的出向/入向关系列表
- `search_results`：关键词搜索时的多个匹配结果
