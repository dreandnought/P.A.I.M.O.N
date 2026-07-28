# engine/rules/inverse.py
"""逆关系推理规则：从正向关系推导逆向关系

本体语义：某些关系天然有逆关系
  A implements B -> B is_implemented_by A
  A contains B -> B is_contained_in A
  A depends_on B -> B is_depended_by A
  ...
"""

from engine.core import Rule
from engine.result import InferenceResult
from models.schema import get_connection


# 逆关系映射表（正向 -> 逆向名称）
INVERSE_MAP = {
    "implements": "is_implemented_by",
    "contains": "is_contained_in",
    "depends_on": "is_depended_by",
    "refines": "is_refined_by",
    "derived_from": "derives",
    "causes": "is_caused_by",
    "constrains": "is_constrained_by",
    "impacts": "is_impacted_by",
}


class InverseRelationRule(Rule):
    name = "inverse_relation"
    description = "从正向关系推导逆向关系"

    def is_applicable(self, entity_ids, db_path=None):
        if not entity_ids:
            return False
        conn = get_connection(db_path)
        placeholders = ",".join("?" * len(entity_ids))
        forward_types = list(INVERSE_MAP.keys())
        type_placeholders = ",".join("?" * len(forward_types))
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM relations r
                WHERE r.type_id IN ({type_placeholders})
                AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
            forward_types + list(entity_ids) + list(entity_ids)
        ).fetchone()
        conn.close()
        return row["cnt"] > 0

    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        entity_set = set(entity_ids)

        for forward, inverse in INVERSE_MAP.items():
            placeholders = ",".join("?" * len(entity_ids))

            rows = conn.execute(
                f"""SELECT r.source_id, r.target_id, r.confidence,
                           e1.name AS source_name, e2.name AS target_name
                    FROM relations r
                    JOIN entities e1 ON r.source_id = e1.id
                    JOIN entities e2 ON r.target_id = e2.id
                    WHERE r.type_id = ?
                    AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
                [forward] + list(entity_ids) + list(entity_ids)
            ).fetchall()

            for row in rows:
                # 只有当 target 在入口实体集合中时，才输出它的逆关系
                if row["target_id"] in entity_set:
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="dependency",
                        source_entity_id=row["target_id"],
                        target_entity_id=row["source_id"],
                        relation_type=inverse,
                        evidence=f"{row['target_name']} {inverse} {row['source_name']} (逆关系推理: 原关系 {row['source_name']} {forward} {row['target_name']})",
                        confidence=row["confidence"],
                        depth=1,
                    ))

        conn.close()
        return results
