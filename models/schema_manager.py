"""
Schema 层管理器。

职责:
1. 数据库迁移（新增 inverse_of, domain_type, range_type 字段）
2. 预置类型层次修正（设置 parent_id）
3. 预置关系语义修正（symmetric, transitive, domain, range）
4. Schema 查看（inspect）
5. Schema 变更执行（execute_schema_plan）
"""

from models.schema import get_connection


def migrate_schema_v2(db_path=None):
    """Schema V2 迁移：新增字段 + 修正预置数据。

    迁移内容:
    - relation_types 表新增 inverse_of, domain_type, range_type 字段
    - 预置实体类型建立 parent_id 层次
    - 预置关系类型修正语义（causes→transitive=1, domain/range 约束等）
    """
    conn = get_connection(db_path)

    # 1. 检查是否已迁移（通过检测 inverse_of 列是否存在）
    cols = {row[1] for row in conn.execute("PRAGMA table_info(relation_types)").fetchall()}
    migrated = "inverse_of" in cols and "domain_type" in cols and "range_type" in cols

    if not migrated:
        # 新增字段
        if "inverse_of" not in cols:
            conn.execute("ALTER TABLE relation_types ADD COLUMN inverse_of TEXT")
        if "domain_type" not in cols:
            conn.execute("ALTER TABLE relation_types ADD COLUMN domain_type TEXT")
        if "range_type" not in cols:
            conn.execute("ALTER TABLE relation_types ADD COLUMN range_type TEXT")
        conn.commit()

    # 2. 修正预置类型层次（设置 parent_id）
    type_hierarchy = {
        "function": "requirement",   # 功能是需求的子类型
        "constraint": "requirement", # 约束是需求的子类型
        "test_case": "requirement",  # 测试用例是需求的子类型
        "interface": "module",       # 接口是模块的子类型
        "data_entity": "actor",      # 数据实体是角色的子类型
    }
    for child, parent in type_hierarchy.items():
        conn.execute(
            "UPDATE entity_types SET parent_id = ? WHERE id = ? AND (parent_id IS NULL OR parent_id != ?)",
            (parent, child, parent)
        )

    # 3. 修正预置关系语义
    relation_updates = [
        # causes 改为 transitive=1（因果有传递性）
        {"id": "causes", "transitive": 1},
        # 设置 domain/range 约束
        {"id": "implements", "domain_type": "function,module", "range_type": "interface,requirement"},
        {"id": "contains", "domain_type": "module,requirement"},
        {"id": "refines", "domain_type": "function", "range_type": "requirement,function"},
        {"id": "constrains", "domain_type": "constraint"},
    ]
    for update in relation_updates:
        rid = update["id"]
        set_parts = []
        values = []
        for k, v in update.items():
            if k == "id":
                continue
            set_parts.append(f"{k} = ?")
            values.append(v)
        values.append(rid)
        if set_parts:
            conn.execute(
                f"UPDATE relation_types SET {', '.join(set_parts)} WHERE id = ?",
                values
            )

    conn.commit()
    conn.close()
    return {"migrated": not migrated, "entity_hierarchy": list(type_hierarchy.items()), "relation_updates": len(relation_updates)}


class SchemaManager:
    """Schema 层管理器：查看和修改实体类型/关系类型 Schema。"""

    def __init__(self, db_path=None):
        self.db_path = db_path

    def inspect(self) -> dict:
        """查看当前 Schema 完整状态。"""
        conn = get_connection(self.db_path)

        # 实体类型层次
        entity_types = []
        rows = conn.execute(
            """SELECT et.id, et.name, et.description, et.parent_id,
                      (SELECT name FROM entity_types WHERE id = et.parent_id) AS parent_name,
                      (SELECT COUNT(*) FROM entities WHERE type_id = et.id AND status = 'active') AS instance_count
               FROM entity_types et ORDER BY et.id"""
        ).fetchall()
        for r in rows:
            entity_types.append(dict(r))

        # 关系类型语义
        relation_types = []
        rows = conn.execute(
            """SELECT rt.id, rt.name, rt.description, rt.symmetric, rt.transitive,
                      rt.inverse_of, rt.domain_type, rt.range_type,
                      (SELECT COUNT(*) FROM relations WHERE type_id = rt.id) AS instance_count
               FROM relation_types rt ORDER BY rt.id"""
        ).fetchall()
        for r in rows:
            relation_types.append(dict(r))

        # 数据库统计
        stats = conn.execute(
            """SELECT (SELECT COUNT(*) FROM entity_types) AS entity_type_count,
                      (SELECT COUNT(*) FROM relation_types) AS relation_type_count,
                      (SELECT COUNT(*) FROM entities WHERE status='active') AS active_entity_count,
                      (SELECT COUNT(*) FROM relations) AS relation_count"""
        ).fetchone()

        conn.close()
        return {
            "entity_types": entity_types,
            "relation_types": relation_types,
            "stats": dict(stats) if stats else {},
        }

    def execute_schema_plan(self, schema_plan: dict) -> dict:
        """执行 Schema 变更计划。

        schema_plan 格式（与 LLM 输出一致）:
        {
            "entity_types": {
                "create": [{"id": "...", "name": "...", "description": "...", "parent_id": "..."}],
                "update": [{"id": "...", "parent_id": "...", "name": "..."}],
                "delete": ["id1", "id2"]
            },
            "relation_types": {
                "create": [{"id": "...", "name": "...", "symmetric": 0, "transitive": 0, ...}],
                "update": [{"id": "...", "symmetric": 1, "domain_type": "..."}],
                "delete": ["id1"]
            }
        }
        """
        conn = get_connection(self.db_path)
        results = {
            "entity_types": {"created": [], "updated": [], "deleted": []},
            "relation_types": {"created": [], "updated": [], "deleted": []},
        }

        # === 实体类型操作 ===
        et_plan = schema_plan.get("entity_types", {})

        # 创建
        for et in et_plan.get("create", []):
            conn.execute(
                """INSERT OR IGNORE INTO entity_types (id, name, description, parent_id, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                (et["id"], et["name"], et.get("description", ""), et.get("parent_id"))
            )
            results["entity_types"]["created"].append(et["id"])

        # 更新
        for et in et_plan.get("update", []):
            allowed = {"name", "description", "parent_id"}
            updates = {k: v for k, v in et.items() if k in allowed and k != "id"}
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE entity_types SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                    list(updates.values()) + [et["id"]]
                )
                results["entity_types"]["updated"].append(et["id"])

        # 删除
        for eid in et_plan.get("delete", []):
            # 检查是否有实例使用
            cnt = conn.execute("SELECT COUNT(*) as cnt FROM entities WHERE type_id = ?", (eid,)).fetchone()
            if cnt and cnt["cnt"] == 0:
                conn.execute("DELETE FROM entity_types WHERE id = ?", (eid,))
                results["entity_types"]["deleted"].append(eid)

        # === 关系类型操作 ===
        rt_plan = schema_plan.get("relation_types", {})

        # 创建
        for rt in rt_plan.get("create", []):
            conn.execute(
                """INSERT OR IGNORE INTO relation_types
                   (id, name, description, symmetric, transitive, inverse_of, domain_type, range_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rt["id"], rt["name"], rt.get("description", ""),
                 rt.get("symmetric", 0), rt.get("transitive", 0),
                 rt.get("inverse_of"), rt.get("domain_type"), rt.get("range_type"))
            )
            results["relation_types"]["created"].append(rt["id"])

        # 更新
        for rt in rt_plan.get("update", []):
            allowed = {"symmetric", "transitive", "inverse_of", "domain_type", "range_type", "name", "description"}
            updates = {k: v for k, v in rt.items() if k in allowed and k != "id"}
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE relation_types SET {set_clause} WHERE id = ?",
                    list(updates.values()) + [rt["id"]]
                )
                results["relation_types"]["updated"].append(rt["id"])

        # 删除
        for rid in rt_plan.get("delete", []):
            cnt = conn.execute("SELECT COUNT(*) as cnt FROM relations WHERE type_id = ?", (rid,)).fetchone()
            if cnt and cnt["cnt"] == 0:
                conn.execute("DELETE FROM relation_types WHERE id = ?", (rid,))
                results["relation_types"]["deleted"].append(rid)

        conn.commit()
        conn.close()
        return results
