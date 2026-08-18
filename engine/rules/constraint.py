# engine/rules/constraint.py
"""约束传播规则：沿包含关系传播约束

本体语义：如果 A constrains B，且 B contains C，则 A 的约束传播到 C
"""

from engine.core import Rule
from engine.result import InferenceResult
from models.schema import get_connection
from models.Istaroth import cte_time_predicates, cte_time_params_list, time_filter_sql


class ConstraintPropagationRule(Rule):
    name = "constraint_propagation"
    description = "沿包含关系传播约束"

    def is_applicable(self, entity_ids, db_path=None):
        if not entity_ids:
            return False
        conn = get_connection(db_path)
        placeholders = ",".join("?" * len(entity_ids))
        # 检查是否存在 constrains 关系或 contains 关系
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM relations r
                WHERE r.type_id IN ('constrains', 'contains')
                AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
            list(entity_ids) + list(entity_ids)
        ).fetchone()
        conn.close()
        return row["cnt"] > 0

    def apply(self, entity_ids, db_path=None, include_future=False, include_expired=False):
        conn = get_connection(db_path)
        results = []

        for eid in entity_ids:
            # 1. 找到直接约束当前实体的约束（时间过滤由 Istaroth 生成）
            time_sql, cparams_extra = time_filter_sql("r", include_future, include_expired)
            cparams = [eid] + cparams_extra

            constraints = conn.execute(
                f"""SELECT r.source_id AS constraint_source, r.confidence,
                          e1.name AS constraint_name,
                          e2.name AS constrained_name
                   FROM relations r
                   JOIN entities e1 ON r.source_id = e1.id
                   JOIN entities e2 ON r.target_id = e2.id
                   WHERE r.target_id = ? AND r.type_id = 'constrains'{time_sql}""",
                cparams
            ).fetchall()

            if not constraints:
                continue

            # 2. 找到当前实体包含的所有子实体（递归 CTE，时间过滤由 Istaroth 生成）
            future_cond, expired_cond = cte_time_predicates(
                "r", include_future, include_expired
            )
            cparams2 = [eid] + cte_time_params_list(include_future, include_expired) * 2
            children = conn.execute(
                f"""WITH RECURSIVE containment AS (
                    SELECT r.target_id FROM relations r
                    WHERE r.source_id = ? AND r.type_id = 'contains'{future_cond}{expired_cond}
                    UNION ALL
                    SELECT r.target_id FROM relations r
                    JOIN containment c ON r.source_id = c.target_id
                    WHERE r.type_id = 'contains'{future_cond}{expired_cond}
                )
                SELECT DISTINCT c.target_id, e.name AS child_name
                FROM containment c
                JOIN entities e ON c.target_id = e.id
                WHERE e.status = 'active'""",
                cparams2
            ).fetchall()

            # 3. 约束传播：父实体的约束传递给子实体
            for constraint in constraints:
                for child in children:
                    # 检查子实体是否已有此约束
                    existing = conn.execute(
                        "SELECT 1 FROM relations WHERE source_id=? AND target_id=? AND type_id='constrains'",
                        (constraint["constraint_source"], child["target_id"])
                    ).fetchone()

                    if not existing:
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="constraint",
                            source_entity_id=constraint["constraint_source"],
                            target_entity_id=child["target_id"],
                            relation_type="constrains",
                            evidence=f"{constraint['constraint_name']} constrains {child['child_name']} (约束传播: 父实体 {constraint['constrained_name']} 被 {constraint['constraint_name']} 约束，约束传播至子实体)",
                            confidence=constraint["confidence"] * 0.85,
                            depth=2,
                        ))

        conn.close()
        return results
