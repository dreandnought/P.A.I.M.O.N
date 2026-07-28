"""
MCP Tool: manage_schema

管理本体 Schema 层：查看/修改实体类型和关系类型。

Actions:
- inspect: 查看当前 Schema（类型层次、关系语义、统计）
- update: 修改 Schema（设置 parent_id, symmetric, transitive, domain, range 等）
- init_hierarchy: 一键初始化预置类型层次和关系语义（数据库迁移）
"""

from typing import Optional
from models.schema_manager import SchemaManager, migrate_schema_v2


def register(mcp):
    """注册 manage_schema 工具到 MCP 服务器。"""

    @mcp.tool()
    def manage_schema(
        action: str = "inspect",
        schema_plan: Optional[dict] = None,
        dry_run: bool = True,
        db_path: Optional[str] = None,
    ) -> dict:
        """**本体 Schema 层管理工具**：查看和修改实体类型/关系类型的定义。

        本工具允许查看当前 Schema（实体类型层次、关系类型语义），
        以及执行 Schema 变更（新增类型、设置 parent_id、修改 symmetric/transitive 等）。

        Schema 层修改默认 dry_run=true，必须先预览变更计划，确认无误后
        再传 dry_run=false 执行。

        ## Args

        - `action`: 操作类型
          - `"inspect"`（默认）：查看当前 Schema 完整状态
          - `"update"`：执行 Schema 变更（需传入 schema_plan）
          - `"init_hierarchy"`：一键初始化预置类型层次和关系语义（数据库迁移）
        - `schema_plan`: 变更计划（action="update" 时需要）
          ```json
          {
            "entity_types": {
              "create": [{"id": "...", "name": "...", "description": "...", "parent_id": "..."}],
              "update": [{"id": "...", "parent_id": "..."}],
              "delete": ["id1"]
            },
            "relation_types": {
              "create": [{"id": "...", "name": "...", "symmetric": 0, "transitive": 0, ...}],
              "update": [{"id": "...", "symmetric": 1, "domain_type": "..."}],
              "delete": ["id1"]
            }
          }
          ```
        - `dry_run`: 预览模式（默认 true）。true 时只计算变更内容不写入数据库
        - `db_path`: 可选，Ontology SQLite 数据库路径

        ## Returns

        - action="inspect": 返回完整的 Schema 状态（类型层次、关系语义、统计）
        - action="update": 返回变更执行结果（created/updated/deleted 列表）
        - action="init_hierarchy": 返回迁移结果
        """
        mgr = SchemaManager(db_path=db_path)

        if action == "inspect":
            return mgr.inspect()

        elif action == "init_hierarchy":
            result = migrate_schema_v2(db_path)
            return {
                "status": "ok",
                "message": "Schema V2 迁移完成，预置类型层次和关系语义已修正",
                "details": result,
            }

        elif action == "update":
            if not schema_plan:
                return {"status": "error", "message": "action='update' 时需要传入 schema_plan 参数"}
            if dry_run:
                # 预览模式：只返回变更计划摘要，不执行
                et_create = len(schema_plan.get("entity_types", {}).get("create", []))
                et_update = len(schema_plan.get("entity_types", {}).get("update", []))
                et_delete = len(schema_plan.get("entity_types", {}).get("delete", []))
                rt_create = len(schema_plan.get("relation_types", {}).get("create", []))
                rt_update = len(schema_plan.get("relation_types", {}).get("update", []))
                rt_delete = len(schema_plan.get("relation_types", {}).get("delete", []))
                return {
                    "status": "dry_run",
                    "message": f"预览: 实体类型({et_create} 创建, {et_update} 更新, {et_delete} 删除) | "
                               f"关系类型({rt_create} 创建, {rt_update} 更新, {rt_delete} 删除)",
                    "schema_plan": schema_plan,
                    "confirm": "如需执行请调用 manage_schema(action='update', dry_run=false, schema_plan=...)",
                }
            else:
                result = mgr.execute_schema_plan(schema_plan)
                return {
                    "status": "ok",
                    "message": "Schema 变更已执行",
                    "details": result,
                }

        else:
            return {"status": "error", "message": f"未知 action: {action}，可选: inspect, update, init_hierarchy"}
