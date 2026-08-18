# engine/rules/conflict.py
"""冲突检测规则：检测本体中的矛盾关系

本体语义：
- 模式1: A depends_on B 且 A conflicts_with B -> 矛盾（依赖且冲突）
- 模式2: A contains B 且 B contains A -> 循环包含
- 模式3: A implements B 且 A conflicts_with B -> 实现且冲突
- 模式4: A constrains B 且 A causes B -> 约束与因果矛盾
"""

from engine.core import Rule
from engine.result import InferenceResult
from models.schema import get_connection
from models.Istaroth import cte_time_predicates, cte_time_params_list, time_filter_sql


class ConflictDetectionRule(Rule):
    name = "conflict_detection"
    description = "检测本体中的矛盾关系"

    def is_applicable(self, entity_ids, db_path=None):
        if not entity_ids:
            return False
        conn = get_connection(db_path)
        placeholders = ",".join("?" * len(entity_ids))
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM relations r
                WHERE (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
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
            entity_row = conn.execute(
                "SELECT name FROM entities WHERE id = ?", (eid,)
            ).fetchone()
            if not entity_row:
                continue
            entity_name = entity_row["name"]

            # 模式1: depends_on 且 conflicts_with 同一实体
            deps = conn.execute(
                f"""SELECT r.target_id, e.name AS target_name
                   FROM relations r JOIN entities e ON r.target_id = e.id
                   WHERE r.source_id = ? AND r.type_id = 'depends_on'{time_sql}""",
                [eid] + time_params
            ).fetchall()

            conflicts = conn.execute(
                f"""SELECT CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END AS other_id,
                          e.name AS other_name
                   FROM relations r
                   JOIN entities e ON
                     (CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END) = e.id
                   WHERE (r.source_id = ? OR r.target_id = ?) AND r.type_id = 'conflicts_with'{time_sql}""",
                [eid, eid, eid, eid] + time_params
            ).fetchall()

            conflict_targets = {c["other_id"] for c in conflicts}
            for dep in deps:
                if dep["target_id"] in conflict_targets:
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="conflict",
                        source_entity_id=eid,
                        target_entity_id=dep["target_id"],
                        relation_type="conflict",
                        evidence=f"{entity_name}({eid}) 同时 depends_on 且 conflicts_with {dep['target_name']}({dep['target_id']}) - 依赖且冲突",
                        confidence=1.0,
                        depth=1,
                    ))

            # 模式2: 循环包含检测（A contains B, B contains ... A）
            future_cond, expired_cond = cte_time_predicates(
                "r", include_future, include_expired
            )
            base_params = cte_time_params_list(include_future, include_expired)
            # 参数顺序：初始 CTE[source_id, base_params...] + 递归 CTE[base_params...] + eid
            cycle_params = [eid] + base_params + base_params + [eid]

            cycle = conn.execute(
                f"""WITH RECURSIVE chain AS (
                    SELECT r.target_id, 1 AS depth FROM relations r
                    WHERE r.source_id = ? AND r.type_id = 'contains'{future_cond}{expired_cond}
                    UNION ALL
                    SELECT r.target_id, c.depth + 1 FROM relations r
                    JOIN chain c ON r.source_id = c.target_id
                    WHERE r.type_id = 'contains' AND c.depth < 20{future_cond}{expired_cond}
                )
                SELECT target_id FROM chain WHERE target_id = ? LIMIT 1""",
                cycle_params
            ).fetchone()

            if cycle:
                results.append(InferenceResult(
                    rule_name=self.name,
                    inference_type="conflict",
                    source_entity_id=eid,
                    target_entity_id=eid,
                    relation_type="circular_contains",
                    evidence=f"{entity_name}({eid}) 存在循环包含链 - 自引用",
                    confidence=1.0,
                    depth=0,
                ))

            # 模式3: implements 且 conflicts_with 同一实体
            impls = conn.execute(
                f"""SELECT r.target_id, e.name AS target_name
                   FROM relations r JOIN entities e ON r.target_id = e.id
                   WHERE r.source_id = ? AND r.type_id = 'implements'{time_sql}""",
                [eid] + time_params
            ).fetchall()

            for impl in impls:
                if impl["target_id"] in conflict_targets:
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="conflict",
                        source_entity_id=eid,
                        target_entity_id=impl["target_id"],
                        relation_type="conflict",
                        evidence=f"{entity_name}({eid}) 同时 implements 且 conflicts_with {impl['target_name']}({impl['target_id']}) - 实现且冲突",
                        confidence=1.0,
                        depth=1,
                    ))

            # 模式4: constrains 且 causes 同一实体
            constrains_targets = {
                r["target_id"] for r in conn.execute(
                    f"""SELECT r.target_id FROM relations r
                       WHERE r.source_id = ? AND r.type_id = 'constrains'{time_sql}""",
                    [eid] + time_params
                ).fetchall()
            }
            causes_targets = {
                r["target_id"] for r in conn.execute(
                    f"""SELECT r.target_id FROM relations r
                       WHERE r.source_id = ? AND r.type_id = 'causes'{time_sql}""",
                    [eid] + time_params
                ).fetchall()
            }

            for tid in constrains_targets & causes_targets:
                target_name = conn.execute(
                    "SELECT name FROM entities WHERE id=?", (tid,)
                ).fetchone()
                tname = target_name["name"] if target_name else tid
                results.append(InferenceResult(
                    rule_name=self.name,
                    inference_type="conflict",
                    source_entity_id=eid,
                    target_entity_id=tid,
                    relation_type="conflict",
                    evidence=f"{entity_name}({eid}) 同时 constrains 且 causes {tname}({tid}) - 约束与因果矛盾",
                    confidence=0.9,
                    depth=1,
                ))

        conn.close()
        return results
