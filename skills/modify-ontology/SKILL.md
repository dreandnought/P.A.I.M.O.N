---
name: "modify-ontology"
description: "根据自然语言描述手动修改本体中的实体和关系（增/删/改）。当用户要求修改、更新本体内容时调用。"
---

# 修改本体

通过 MCP 工具 `modify_ontology` 根据自然语言描述修改本体中的实体和关系，支持创建、更新、删除实体与关系。

## 触发条件

- 用户说"把 xxx 实体的描述更新为 xxx"
- 用户要求添加/修改/删除某个实体或关系
- 用户要求用自然语言描述对已有本体的变更

## MCP 工具

- 工具名：`modify_ontology`
- 参数：
  - `description`（必填）：自然语言变更描述，例如"把用户登录的描述更新为支持微信扫码"
  - `target_entity_id`（可选）：优先修改的目标实体 ID
  - `dry_run`（可选，默认 `true`）：为 `true` 时只返回变更计划，不写入数据库；为 `false` 时执行写入
  - `plan`（可选）：传入上一次 `dry_run=true` 返回的 plan，配合 `dry_run=false` 直接执行该计划，避免两次 LLM 调用结果不一致
  - `db_path`（可选）：数据库路径，通常不传

## 用法示例

第一步，预览变更计划（`dry_run=true`）：

```json
{
  "name": "modify_ontology",
  "arguments": {
    "description": "把用户登录的描述更新为支持手机号、邮箱和微信扫码登录，并添加用户登录依赖微信 OAuth 接口的关系",
    "target_entity_id": "func:user_login",
    "dry_run": true
  }
}
```

第二步，确认计划无误后执行写入（`dry_run=false`，回传上一步的 plan）：

```json
{
  "name": "modify_ontology",
  "arguments": {
    "description": "把用户登录的描述更新为支持手机号、邮箱和微信扫码登录，并添加用户登录依赖微信 OAuth 接口的关系",
    "plan": { "...": "上一步返回的 plan 原样回传" },
    "dry_run": false
  }
}
```

## 返回值

- `dry_run=true` 时：
  - `plan`：变更计划（含将创建/更新/删除的实体与关系）
  - `validation`：计划校验结果（`valid`、`errors`）
- `dry_run=false` 时：
  - `success`：是否全部执行成功
  - `created_entities` / `updated_entities` / `deleted_entities`：实体变更明细
  - `created_relations` / `updated_relations` / `deleted_relations`：关系变更明细
  - `failed_items`：执行失败的条目

## 注意事项

- 必须先以 `dry_run=true` 预览变更计划，经用户确认后再以 `dry_run=false` 执行写入
- 执行写入时建议回传预览阶段返回的 `plan`，确保执行的与预览的完全一致
- 校验不通过（`validation.valid=false`）时不会写入，需根据 `errors` 调整描述后重试

## 常见问题

**关系方向错误**：计划中关系的方向由 LLM 根据描述推断，偶发弄反。审核 dry_run 计划时，
重点检查 `create`/`update`/`delete` 中 `source_id`/`target_id` 或 `relation_id` 的方向是否符合预期
（如"A 包含 B"应为 `contains:A->B`，不能是 `contains:B->A`）。
若校验报"关系不存在"而该关系确实存在，通常是方向被弄反，修正描述（明确"A 的 X 关系指向 B"）后重试。

**按名称定位实体**：工具参数没有 `target_entity_name`，名称定位依赖 LLM 解析 `description` 中的实体名称。
建议在描述中使用库中实体的准确名称，并同时传 `target_entity_id` 双保险。

**实体不存在**：计划允许在创建关系的同时创建缺失的实体（`entities.create`），
在 dry_run 阶段确认将要新建的实体及其类型、ID 是否符合预期。
