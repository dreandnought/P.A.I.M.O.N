---
name: "delete-ontology-entity"
description: "删除本体中的指定实体及其所有关联关系。当用户要求删除某个本体实体时调用。"
---

# 删除本体实体

通过 MCP 工具 `delete_ontology_entity` 直接删除本体中的指定实体及其关联关系。

## 触发条件

- 用户说“删除本体中的 xxx”
- 用户想移除某个实体及其所有关系
- 与 `modify_ontology` 不同，此工具用于精确、直接地删除单个实体

## MCP 工具

- 工具名：`delete_ontology_entity`
- 参数：
  - `entity_id`（可选）：实体 ID，优先使用
  - `entity_name`（可选）：实体名称，当未提供 entity_id 时使用
  - `db_path`（可选）：数据库路径，通常不传

## 用法示例

按 ID 删除：

```json
{
  "name": "delete_ontology_entity",
  "arguments": {
    "entity_id": "actor:anthropic_ceo"
  }
}
```

按名称删除：

```json
{
  "name": "delete_ontology_entity",
  "arguments": {
    "entity_name": "旧版短信服务"
  }
}
```

## 返回值

- `success`：是否删除成功
- `deleted_entity_id`：被删除的实体 ID
- `message`：操作结果说明

## 注意事项

- 删除实体时会级联删除其所有出向和入向关系
- 删除前请确认实体 ID 或名称正确，避免误删
