# engine/rules/transitive.py
"""传递闭包规则：对可传递关系类型计算传递闭包

本体语义：如果关系类型声明为 transitive=1，则 A->B 且 B->C 可推导出 A->C
适用关系类型：depends_on, contains, derived_from
"""

from engine.core import Rule
from engine.result import InferenceResult
from models.schema import get_connection
from models.Istaroth import cte_time_predicates, cte_time_params_list


class TransitiveClosureRule(Rule):
    name = "transitive_closure"
    description = "对可传递关系类型计算传递闭包"

    def is_applicable(self, entity_ids, db_path=None):
        if not entity_ids:
            return False
        conn = get_connection(db_path)
        placeholders = ",".join("?" * len(entity_ids))
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM relations r
                JOIN relation_types rt ON r.type_id = rt.id
                WHERE rt.transitive = 1
                AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
            entity_ids + entity_ids
        ).fetchone()
        conn.close()
        return row["cnt"] > 0

    def apply(self, entity_ids, db_path=None, include_future=False, include_expired=False):
        conn = get_connection(db_path)
        results = []
        max_depth = 10

        # 查询所有 transitive=1 的关系类型
        transitive_types = conn.execute(
            "SELECT id, name FROM relation_types WHERE transitive = 1"
        ).fetchall()

        for rt in transitive_types:
            rtype = rt["id"]
            rtype_name = rt["name"]

            # 递归 CTE 中过滤未来/过期关系（由 Istaroth 统一生成谓词与参数）
            future_cond, expired_cond = cte_time_predicates(
                "r", include_future, include_expired
            )
            base_params = cte_time_params_list(include_future, include_expired)

            for eid in entity_ids:
                # 初始 CTE 参数 + 递归 CTE 参数（同一组 now 参数在两个位置复用）
                params = [eid, rtype] + base_params + [rtype, max_depth] + base_params

                # 用递归 CTE 计算传递闭包
                rows = conn.execute(
                    f"""WITH RECURSIVE closure AS (
                        SELECT target_id, 1 AS depth, source_id AS path_start
                        FROM relations r
                        WHERE source_id = ? AND type_id = ?{future_cond}{expired_cond}
                        UNION ALL
                        SELECT r.target_id, c.depth + 1, c.path_start
                        FROM relations r
                        JOIN closure c ON r.source_id = c.target_id
                        WHERE r.type_id = ? AND c.depth < ?{future_cond}{expired_cond}
                    )
                    SELECT DISTINCT c.target_id, c.depth, c.path_start,
                           e.name AS target_name
                    FROM closure c
                    JOIN entities e ON c.target_id = e.id
                    WHERE c.target_id != c.path_start
                      AND e.status = 'active'
                    ORDER BY c.depth""",
                    params
                ).fetchall()

                for row in rows:
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="dependency",
                        source_entity_id=eid,
                        target_entity_id=row["target_id"],
                        relation_type=rtype,
                        evidence=f"{eid} --{rtype_name}({row['depth']}跳)--> {row['target_id']} (传递闭包)",
                        confidence=max(0.3, 1.0 / (1.0 + row["depth"] * 0.15)),
                        depth=row["depth"],
                    ))

        conn.close()
        return results
