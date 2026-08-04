# engine/checker.py
"""本体一致性检查器

检查推理结果和已有数据的一致性：
1. 类型兼容性检查 - 关系两端实体类型是否合理
2. 孤立实体检查 - 没有任何关系的实体
3. 推理结果重复检查
"""

from engine.core import Checker
from engine.result import InferenceResult
from models.schema import get_connection
from datetime import datetime, timezone


# 类型兼容性规则：哪些关系类型可以连接哪些实体类型对
# (source_type, relation_type, target_type) - 允许的组合
COMPATIBLE_PAIRS = {
    # depends_on: function/requirement/module 可以依赖任何类型
    ("function", "depends_on", "module"),
    ("function", "depends_on", "interface"),
    ("function", "depends_on", "function"),
    ("requirement", "depends_on", "requirement"),
    ("module", "depends_on", "module"),
    ("module", "depends_on", "interface"),
    # contains: module/requirement 可以包含子项
    ("module", "contains", "function"),
    ("module", "contains", "module"),
    ("requirement", "contains", "requirement"),
    # implements: function 实现 interface/requirement
    ("function", "implements", "interface"),
    ("function", "implements", "requirement"),
    ("module", "implements", "interface"),
    # constrains: constraint 约束其他实体
    ("constraint", "constrains", "function"),
    ("constraint", "constrains", "requirement"),
    ("constraint", "constrains", "module"),
    # conflicts_with: 任意类型之间
    # relates_to: 任意类型之间
}

# 明确不兼容的组合（会发出警告）
INCOMPATIBLE_PAIRS = {
    ("actor", "depends_on", "test_case"),
    ("actor", "implements", "test_case"),
    ("test_case", "contains", "module"),
    ("data_entity", "implements", "function"),
    ("test_case", "depends_on", "actor"),
    ("actor", "contains", "module"),
    ("data_entity", "contains", "module"),
}

# 类型层级映射：子类型可以自动兼容父类型允许的关系
TYPE_HIERARCHY = {
    "function": "requirement",
    "interface": "module",
    "data_entity": "actor",
    "constraint": "requirement",
    "test_case": "requirement",
}


class ConsistencyChecker(Checker):
    """本体一致性检查器"""

    name = "consistency_checker"

    def check(self, entity_ids, inferences, db_path=None, include_future=False, include_expired=False):
        issues = []
        issues.extend(self._check_type_compatibility(entity_ids, db_path, include_future, include_expired))
        issues.extend(self._check_orphan_entities(entity_ids, db_path))
        issues.extend(self._check_inference_consistency(inferences, db_path))
        issues.extend(self._check_circular_dependencies(entity_ids, db_path, include_future, include_expired))
        issues.extend(self._check_confidence_anomalies(inferences, db_path))
        return issues

    def _check_type_compatibility(self, entity_ids, db_path=None, include_future=False, include_expired=False):
        """检查关系两端实体类型是否兼容"""
        conn = get_connection(db_path)
        issues = []
        now = datetime.now(timezone.utc).isoformat()

        placeholders = ",".join("?" * len(entity_ids))
        conditions = []
        params = list(entity_ids) + list(entity_ids)
        if not include_future:
            conditions.append("(r.valid_from IS NULL OR r.valid_from <= ?)")
            params.append(now)
        if not include_expired:
            conditions.append("(r.valid_until IS NULL OR r.valid_until > ?)")
            params.append(now)
        time_sql = (" AND " + " AND ".join(conditions)) if conditions else ""

        rows = conn.execute(
            f"""SELECT r.source_id, r.target_id, r.type_id,
                      e1.type_id AS source_type, e2.type_id AS target_type,
                      e1.name AS source_name, e2.name AS target_name
               FROM relations r
               JOIN entities e1 ON r.source_id = e1.id
               JOIN entities e2 ON r.target_id = e2.id
               WHERE (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders})){time_sql}""",
            params
        ).fetchall()

        for row in rows:
            pair = (row["source_type"], row["type_id"], row["target_type"])
            if pair in INCOMPATIBLE_PAIRS:
                issues.append(InferenceResult(
                    rule_name=self.name,
                    inference_type="conflict",
                    source_entity_id=row["source_id"],
                    target_entity_id=row["target_id"],
                    relation_type="type_incompatible",
                    evidence=f"类型不兼容: {row['source_name']}({row['source_type']}) {row['type_id']} {row['target_name']}({row['target_type']}) - 此关系组合通常不合理",
                    confidence=0.8,
                    depth=1,
                ))

        conn.close()
        return issues

    def _check_orphan_entities(self, entity_ids, db_path=None):
        """检查孤立实体（没有任何关系的实体）"""
        conn = get_connection(db_path)
        issues = []

        for eid in entity_ids:
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM relations
                   WHERE source_id = ? OR target_id = ?""",
                (eid, eid)
            ).fetchone()

            if row["cnt"] == 0:
                entity = conn.execute(
                    "SELECT name FROM entities WHERE id = ?", (eid,)
                ).fetchone()
                name = entity["name"] if entity else eid
                issues.append(InferenceResult(
                    rule_name=self.name,
                    inference_type="conflict",
                    source_entity_id=eid,
                    target_entity_id=eid,
                    relation_type="orphan_entity",
                    evidence=f"实体 {name}({eid}) 没有任何关系连接 - 孤立实体",
                    confidence=0.5,
                    depth=0,
                ))

        conn.close()
        return issues

    def _check_inference_consistency(self, inferences, db_path=None):
        """检查推理结果内部的矛盾"""
        issues = []

        # 检查推理结果中是否有自相矛盾
        # 如：同一对实体同时推理出 depends_on 和 conflicts_with
        by_pair = {}
        for inf in inferences:
            key = frozenset([inf.source_entity_id, inf.target_entity_id])
            by_pair.setdefault(key, []).append(inf)

        for pair, infs in by_pair.items():
            types = {inf.relation_type for inf in infs}
            if "depends_on" in types and "conflicts_with" in types:
                for inf in infs:
                    if inf.relation_type in ("depends_on", "conflicts_with"):
                        issues.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="conflict",
                            source_entity_id=inf.source_entity_id,
                            target_entity_id=inf.target_entity_id,
                            relation_type="inference_conflict",
                            evidence=f"推理结果矛盾: 同一对实体同时存在 depends_on 和 conflicts_with 推理 [{inf.rule_name}]",
                            confidence=0.9,
                            depth=inf.depth,
                        ))
            # 新增：implements + conflicts_with 矛盾
            if "implements" in types and "conflicts_with" in types:
                for inf in infs:
                    if inf.relation_type in ("implements", "conflicts_with"):
                        issues.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="conflict",
                            source_entity_id=inf.source_entity_id,
                            target_entity_id=inf.target_entity_id,
                            relation_type="inference_conflict",
                            evidence=f"推理结果矛盾: 同一对实体同时存在 implements 和 conflicts_with 推理 [{inf.rule_name}]",
                            confidence=0.85,
                            depth=inf.depth,
                        ))

        return issues

    def _check_circular_dependencies(self, entity_ids, db_path=None, include_future=False, include_expired=False):
        """检测循环依赖链（A->B->C->A）"""
        conn = get_connection(db_path)
        issues = []
        now = datetime.now(timezone.utc).isoformat()

        future_cond = "" if include_future else " AND (r.valid_from IS NULL OR r.valid_from <= ?) "
        expired_cond = "" if include_expired else " AND (r.valid_until IS NULL OR r.valid_until > ?) "

        for eid in entity_ids:
            # 使用递归 CTE 检测 depends_on 循环
            try:
                # 参数顺序：初始CTE[source_id, valid_from?, valid_until?] + 递归CTE[valid_from?, valid_until?]
                future_cond = "" if include_future else " AND (r.valid_from IS NULL OR r.valid_from <= ?) "
                expired_cond = "" if include_expired else " AND (r.valid_until IS NULL OR r.valid_until > ?) "
                params = [eid]
                if not include_future:
                    params.append(now)
                if not include_expired:
                    params.append(now)
                if not include_future:
                    params.append(now)
                if not include_expired:
                    params.append(now)
                cycle = conn.execute(
                    f"""WITH RECURSIVE dep_chain AS (
                        SELECT r.target_id, 1 AS depth, r.source_id AS path_start
                        FROM relations r
                        WHERE r.source_id = ? AND r.type_id = 'depends_on'{future_cond}{expired_cond}
                        UNION ALL
                        SELECT r.target_id, c.depth + 1, c.path_start
                        FROM relations r
                        JOIN dep_chain c ON r.source_id = c.target_id
                        WHERE r.type_id = 'depends_on' AND c.depth < 20{future_cond}{expired_cond}
                    )
                    SELECT target_id, depth FROM dep_chain WHERE target_id = path_start LIMIT 1""",
                    params
                ).fetchone()

                if cycle:
                    entity_name = conn.execute(
                        "SELECT name FROM entities WHERE id=?", (eid,)
                    ).fetchone()
                    name = entity_name["name"] if entity_name else eid
                    issues.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="conflict",
                        source_entity_id=eid,
                        target_entity_id=eid,
                        relation_type="circular_dependency",
                        evidence=f"检测到循环依赖: {name}({eid}) 经过 {cycle['depth']} 跳后回到自身",
                        confidence=1.0,
                        depth=cycle["depth"],
                    ))
            except Exception:
                pass

        conn.close()
        return issues

    def _check_confidence_anomalies(self, inferences, db_path=None):
        """检测置信度异常（过高或过低的推理结果）"""
        issues = []

        for inf in inferences:
            # 置信度过低的推理可能不可靠
            if inf.confidence < 0.2:
                issues.append(InferenceResult(
                    rule_name=self.name,
                    inference_type="conflict",
                    source_entity_id=inf.source_entity_id,
                    target_entity_id=inf.target_entity_id,
                    relation_type="low_confidence",
                    evidence=f"推理置信度过低 ({inf.confidence:.2f}): {inf.evidence} [{inf.rule_name}]",
                    confidence=inf.confidence,
                    depth=inf.depth,
                ))
            # 深度过深的推理可能不准确
            elif inf.depth >= 5 and inf.confidence > 0.7:
                issues.append(InferenceResult(
                    rule_name=self.name,
                    inference_type="conflict",
                    source_entity_id=inf.source_entity_id,
                    target_entity_id=inf.target_entity_id,
                    relation_type="suspicious_depth",
                    evidence=f"推理深度={inf.depth} 但置信度={inf.confidence:.2f} 偏高，建议人工验证 [{inf.rule_name}]",
                    confidence=0.5,
                    depth=inf.depth,
                ))

        return issues
