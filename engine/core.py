# engine/core.py
"""推理引擎核心：Rule 基类 + ReasoningEngine"""

from abc import ABC, abstractmethod
from typing import Optional
from .result import InferenceResult, ReasoningOutput


class Rule(ABC):
    """推理规则基类"""

    name: str = "base_rule"
    description: str = ""

    @abstractmethod
    def apply(self, entity_ids: list, db_path=None) -> list:
        """对给定实体列表应用此规则，返回推理结果列表 (list[InferenceResult])"""
        ...

    def is_applicable(self, entity_ids: list, db_path=None) -> bool:
        """检查此规则是否适用于当前实体集合。默认 True，子类可覆盖。"""
        return len(entity_ids) > 0


class Checker(ABC):
    """一致性检查器基类"""

    name: str = "base_checker"

    @abstractmethod
    def check(self, entity_ids: list, inferences: list, db_path=None) -> list:
        """检查一致性，返回冲突列表 (list[InferenceResult])"""
        ...


class ReasoningEngine:
    """推理引擎：管理规则的注册、执行和结果汇总"""

    def __init__(self, db_path=None):
        self.db_path = db_path
        self.rules: list = []
        self.checkers: list = []

    def register_rule(self, rule: Rule):
        """注册推理规则"""
        self.rules.append(rule)

    def register_checker(self, checker: Checker):
        """注册一致性检查器"""
        self.checkers.append(checker)

    def run(self, entity_ids: list) -> ReasoningOutput:
        """执行推理流水线

        Args:
            entity_ids: 入口实体 ID 列表（LLM 抽取+匹配后的结果）

        Returns:
            ReasoningOutput: 完整推理结果
        """
        if not entity_ids:
            return ReasoningOutput(
                entity_ids=[],
                inferences=[],
                conflicts=[],
                subgraph={"entities": [], "relations": []},
                stats={"rules_executed": 0, "inferences_count": 0, "conflicts_count": 0},
            )

        all_inferences = []
        all_conflicts = []
        subgraph_entities = set(entity_ids)

        # 1. 执行所有规则
        rules_executed = 0
        for rule in self.rules:
            try:
                if not rule.is_applicable(entity_ids, self.db_path):
                    continue
                results = rule.apply(entity_ids, self.db_path)
                all_inferences.extend(results)
                rules_executed += 1
                # 收集推理涉及的实体
                for r in results:
                    subgraph_entities.add(r.source_entity_id)
                    subgraph_entities.add(r.target_entity_id)
            except Exception as e:
                # 单个规则失败不应影响其他规则
                import sys
                print(f"[ReasoningEngine] 规则 {rule.name} 执行失败: {e}", file=sys.stderr)

        # 2. 执行一致性检查
        for checker in self.checkers:
            try:
                conflicts = checker.check(entity_ids, all_inferences, self.db_path)
                all_conflicts.extend(conflicts)
            except Exception as e:
                import sys
                print(f"[ReasoningEngine] 检查器 {checker.name} 执行失败: {e}", file=sys.stderr)

        # 3. 去重：相同 (rule, source, target, type) 的推理结果只保留置信度最高的
        seen = {}
        for inf in all_inferences:
            key = (inf.rule_name, inf.source_entity_id, inf.target_entity_id, inf.relation_type)
            if key not in seen or inf.confidence > seen[key].confidence:
                seen[key] = inf
        all_inferences = list(seen.values())

        # 4. 构建子图
        subgraph = self._build_subgraph(list(subgraph_entities))

        # 5. 统计
        stats = {
            "rules_executed": rules_executed,
            "inferences_count": len(all_inferences),
            "conflicts_count": len(all_conflicts),
            "subgraph_size": len(subgraph_entities),
            "by_type": {
                "dependency": len([r for r in all_inferences if r.inference_type == "dependency"]),
                "constraint": len([r for r in all_inferences if r.inference_type == "constraint"]),
                "impact": len([r for r in all_inferences if r.inference_type == "impact"]),
                "conflict": len([r for r in all_inferences if r.inference_type == "conflict"]),
            },
        }

        return ReasoningOutput(
            entity_ids=entity_ids,
            inferences=all_inferences,
            conflicts=all_conflicts,
            subgraph=subgraph,
            stats=stats,
        )

    def _build_subgraph(self, entity_ids: list, max_depth: int = 2) -> dict:
        """从实体列表构建子图（BFS 多跳扩展，含实体 + 直接关系）。

        与 V1 _graph_search 一致：对可传递关系类型做多跳 BFS 扩展，
        收集所有关联实体和它们之间的直接关系，供 LLM 融合阶段使用。

        Args:
            entity_ids: 入口实体 ID 列表（规则引擎推理涉及的所有实体）
            max_depth: BFS 最大扩展深度，默认 2

        Returns:
            dict: {"entities": [...], "relations": [...]}
        """
        from models.schema import get_connection

        if not entity_ids:
            return {"entities": [], "relations": []}

        conn = get_connection(self.db_path)

        # 可传递关系类型（用于 BFS 多跳扩展）
        transitive_types = ["depends_on", "contains", "derived_from"]

        # 步骤1：BFS 多跳扩展——从入口实体出发，沿可传递关系向外扩展
        expanded_ids = set(entity_ids)
        seen_relation_keys = set()
        all_relations = []

        for eid in entity_ids:
            expanded_ids.add(eid)

            # 1.1 一跳直接关系（全部类型）
            rows = conn.execute(
                """SELECT r.id, r.type_id, r.source_id, r.target_id,
                          r.confidence, r.weight,
                          rt.name AS relation_type,
                          rt.symmetric, rt.transitive,
                          e1.name AS source_name,
                          e2.name AS target_name
                   FROM relations r
                   JOIN relation_types rt ON r.type_id = rt.id
                   JOIN entities e1 ON r.source_id = e1.id
                   JOIN entities e2 ON r.target_id = e2.id
                   WHERE (r.source_id = ? OR r.target_id = ?)
                     AND e1.status = 'active'
                     AND e2.status = 'active'""",
                (eid, eid)
            ).fetchall()
            for r in rows:
                rkey = r["id"]
                if rkey not in seen_relation_keys:
                    seen_relation_keys.add(rkey)
                    all_relations.append({
                        "id": r["id"],
                        "type": r["type_id"],
                        "source_id": r["source_id"],
                        "target_id": r["target_id"],
                        "source_name": r["source_name"],
                        "target_name": r["target_name"],
                        "confidence": r["confidence"],
                        "weight": r["weight"],
                        "symmetric": bool(r["symmetric"]),
                        "transitive": bool(r["transitive"]),
                    })
                # 收集对端实体
                related_id = r["target_id"] if r["source_id"] == eid else r["source_id"]
                if related_id:
                    expanded_ids.add(related_id)

            # 1.2 多跳 BFS（仅可传递关系类型）
            for rtype in transitive_types:
                try:
                    rows = conn.execute(
                        """WITH RECURSIVE transitive_bfs AS (
                            SELECT target_id, 1 AS depth
                            FROM relations
                            WHERE source_id = ? AND type_id = (
                                SELECT id FROM relation_types WHERE name = ?
                            )
                            UNION ALL
                            SELECT r.target_id, t.depth + 1
                            FROM relations r
                            JOIN transitive_bfs t ON r.source_id = t.target_id
                            WHERE r.type_id = (
                                SELECT id FROM relation_types WHERE name = ?
                            ) AND t.depth < ?
                        )
                        SELECT DISTINCT tb.target_id, tb.depth,
                               e.name AS target_name,
                               et.name AS target_type
                        FROM transitive_bfs tb
                        JOIN entities e ON tb.target_id = e.id
                        JOIN entity_types et ON e.type_id = et.id
                        WHERE tb.target_id != ?
                          AND e.status = 'active'
                        ORDER BY tb.depth""",
                        (eid, rtype, rtype, max_depth, eid)
                    ).fetchall()

                    for row in rows:
                        target_id = row["target_id"]
                        expanded_ids.add(target_id)
                        rkey = f"{eid}--{rtype}(bfs:{row['depth']})-->{target_id}"
                        if rkey not in seen_relation_keys:
                            seen_relation_keys.add(rkey)
                            all_relations.append({
                                "id": rkey,
                                "type": rtype,
                                "source_id": eid,
                                "target_id": target_id,
                                "source_name": conn.execute(
                                    "SELECT name FROM entities WHERE id=?", (eid,)
                                ).fetchone()["name"],
                                "target_name": row["target_name"],
                                "relation_type": rtype,
                                "confidence": max(0.3, 1.0 / (1.0 + row["depth"] * 0.15)),
                                "depth": row["depth"],
                                "transitive_bfs": True,
                            })
                except Exception:
                    pass  # 某个关系类型不存在则跳过

        # 步骤2：查询所有扩展到的实体的详情
        all_ids = list(expanded_ids)
        entities = []
        if all_ids:
            placeholders = ",".join("?" * len(all_ids))
            rows = conn.execute(
                f"""SELECT e.id, e.name, e.type_id, e.description,
                          et.name AS type_name
                   FROM entities e
                   JOIN entity_types et ON e.type_id = et.id
                   WHERE e.id IN ({placeholders}) AND e.status = 'active'""",
                all_ids
            ).fetchall()
            for r in rows:
                entities.append({
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["type_id"],
                    "type_name": r["type_name"],
                    "description": (r["description"] or "")[:120],
                })

        # 步骤3：查询扩展到的实体之间的直接关系（补齐步骤1.1漏掉的内部关系）
        existing_rids = set(r["id"] for r in all_relations)
        if len(all_ids) > 1:
            placeholders = ",".join("?" * len(all_ids))
            rows = conn.execute(
                f"""SELECT r.id, r.type_id, r.source_id, r.target_id,
                          r.confidence, r.weight,
                          rt.name AS relation_type,
                          rt.symmetric, rt.transitive,
                          e1.name AS source_name,
                          e2.name AS target_name
                   FROM relations r
                   JOIN relation_types rt ON r.type_id = rt.id
                   JOIN entities e1 ON r.source_id = e1.id
                   JOIN entities e2 ON r.target_id = e2.id
                   WHERE r.source_id IN ({placeholders})
                     AND r.target_id IN ({placeholders})
                     AND e1.status = 'active'
                     AND e2.status = 'active'""",
                all_ids + all_ids
            ).fetchall()
            for r in rows:
                if r["id"] not in existing_rids:
                    all_relations.append({
                        "id": r["id"],
                        "type": r["type_id"],
                        "source_id": r["source_id"],
                        "target_id": r["target_id"],
                        "source_name": r["source_name"],
                        "target_name": r["target_name"],
                        "confidence": r["confidence"],
                        "weight": r["weight"],
                        "symmetric": bool(r["symmetric"]),
                        "transitive": bool(r["transitive"]),
                    })

        conn.close()
        return {"entities": entities, "relations": all_relations}
