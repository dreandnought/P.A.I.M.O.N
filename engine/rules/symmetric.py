# engine/rules/symmetric.py
"""对称推理规则：对对称关系类型自动推导反向关系

本体语义：如果关系类型声明为 symmetric=1，则 A->B 可推导出 B->A
适用关系类型：conflicts_with, relates_to
"""

from engine.core import Rule
from engine.result import InferenceResult
from models.schema import get_connection
from models.Istaroth import time_filter_sql


class SymmetricRule(Rule):
    name = "symmetric_inference"
    description = "对对称关系类型自动推导反向关系"

    def is_applicable(self, entity_ids, db_path=None):
        if not entity_ids:
            return False
        conn = get_connection(db_path)
        placeholders = ",".join("?" * len(entity_ids))
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM relations r
                JOIN relation_types rt ON r.type_id = rt.id
                WHERE rt.symmetric = 1
                AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
            entity_ids + entity_ids
        ).fetchone()
        conn.close()
        return row["cnt"] > 0

    def apply(self, entity_ids, db_path=None, include_future=False, include_expired=False):
        conn = get_connection(db_path)
        results = []
        entity_set = set(entity_ids)

        symmetric_types = conn.execute(
            "SELECT id, name FROM relation_types WHERE symmetric = 1"
        ).fetchall()

        for rt in symmetric_types:
            rtype = rt["id"]
            rtype_name = rt["name"]
            placeholders = ",".join("?" * len(entity_ids))

            # 时间过滤条件（由 Istaroth 统一生成）
            time_sql, time_params = time_filter_sql("r", include_future, include_expired)
            params = [rtype] + list(entity_ids) + list(entity_ids) + time_params

            # 查询涉及入口实体的对称关系
            rows = conn.execute(
                f"""SELECT r.source_id, r.target_id, r.confidence,
                           e1.name AS source_name, e2.name AS target_name
                    FROM relations r
                    JOIN entities e1 ON r.source_id = e1.id
                    JOIN entities e2 ON r.target_id = e2.id
                    WHERE r.type_id = ?
                    AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders})){time_sql}""",
                params
            ).fetchall()

            seen = set()
            for row in rows:
                # 构造反向关系
                reverse_key = (row["target_id"], rtype, row["source_id"])
                if reverse_key in seen:
                    continue
                seen.add(reverse_key)

                # 检查反向关系是否已在数据库中存在
                exists = conn.execute(
                    "SELECT 1 FROM relations WHERE source_id=? AND target_id=? AND type_id=?",
                    (row["target_id"], row["source_id"], rtype)
                ).fetchone()

                if not exists:
                    # 只有当至少一端在入口实体集合中时才输出
                    if row["target_id"] in entity_set or row["source_id"] in entity_set:
                        inference_type = "conflict" if rtype == "conflicts_with" else "dependency"
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type=inference_type,
                            source_entity_id=row["target_id"],
                            target_entity_id=row["source_id"],
                            relation_type=rtype,
                            evidence=f"{row['target_name']} --{rtype_name}--> {row['source_name']} (对称推理: 原关系 {row['source_name']}->{rtype_name}->{row['target_name']})",
                            confidence=row["confidence"],
                            depth=1,
                        ))

        conn.close()
        return results
