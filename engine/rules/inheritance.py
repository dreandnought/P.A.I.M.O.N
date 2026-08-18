# engine/rules/inheritance.py
"""类型继承推理规则：沿类型层级推导兄弟实体的共性关系

本体语义：如果实体类型 B 的 parent_id 指向 A，则 B 类型的实体继承 A 类型定义的属性约束。
兄弟实体（同类型）之间可能存在共性关系模式。
"""

from engine.core import Rule
from engine.result import InferenceResult
from models.schema import get_connection
from models.Istaroth import time_filter_sql


class InheritanceRule(Rule):
    name = "type_inheritance"
    description = "沿类型层级继承关系约束，推导兄弟实体的共性关系"

    def is_applicable(self, entity_ids, db_path=None):
        if not entity_ids:
            return False
        conn = get_connection(db_path)
        # 检查是否有兄弟实体存在关系
        placeholders = ",".join("?" * len(entity_ids))
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM entities e
                WHERE e.type_id IN (
                    SELECT type_id FROM entities WHERE id IN ({placeholders})
                )
                AND e.id NOT IN ({placeholders})
                AND e.status = 'active'""",
            list(entity_ids) + list(entity_ids)
        ).fetchone()
        conn.close()
        return row["cnt"] > 0

    def apply(self, entity_ids, db_path=None, include_future=False, include_expired=False):
        conn = get_connection(db_path)
        results = []

        # 时间过滤条件（由 Istaroth 统一生成）
        time_sql, time_params = time_filter_sql("r", include_future, include_expired)

        for eid in entity_ids:
            entity = conn.execute(
                "SELECT type_id, name FROM entities WHERE id = ?", (eid,)
            ).fetchone()
            if not entity:
                continue

            type_id = entity["type_id"]

            # 1. 递归查询所有父类型
            parent_types = conn.execute(
                """WITH RECURSIVE type_tree AS (
                    SELECT id, parent_id FROM entity_types WHERE id = ?
                    UNION ALL
                    SELECT et.id, et.parent_id FROM entity_types et
                    JOIN type_tree tt ON et.id = tt.parent_id
                )
                SELECT id FROM type_tree WHERE id != ?""",
                (type_id, type_id)
            ).fetchall()

            # 2. 查询同类型的兄弟实体（限制数量）
            siblings = conn.execute(
                """SELECT e2.id, e2.name
                   FROM entities e2
                   WHERE e2.type_id = ? AND e2.id != ? AND e2.status = 'active'
                   LIMIT 10""",
                (type_id, eid)
            ).fetchall()

            # 3. 如果兄弟实体有共同的关系模式，推理当前实体可能也有类似关系
            for sibling in siblings:
                sibling_relations = conn.execute(
                    f"""SELECT r.type_id, r.target_id, r.confidence,
                              e.name AS target_name
                       FROM relations r
                       JOIN entities e ON r.target_id = e.id
                       WHERE r.source_id = ?
                       AND e.status = 'active'{time_sql}""",
                    [sibling["id"]] + time_params
                ).fetchall()

                for rel in sibling_relations:
                    # 检查当前实体是否已有这种关系
                    existing = conn.execute(
                        "SELECT 1 FROM relations WHERE source_id=? AND type_id=? AND target_id=?",
                        (eid, rel["type_id"], rel["target_id"])
                    ).fetchone()

                    if not existing:
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="dependency",
                            source_entity_id=eid,
                            target_entity_id=rel["target_id"],
                            relation_type=rel["type_id"],
                            evidence=f"兄弟实体 {sibling['name']}({sibling['id']}) 具有 {rel['type_id']}->{rel['target_name']} 关系，当前实体 {entity['name']}({eid}) 可能也有 (类型继承推理)",
                            confidence=0.3,
                            depth=1,
                        ))

        conn.close()
        return results
