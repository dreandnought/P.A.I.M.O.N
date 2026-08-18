# engine/rules/impact.py
"""影响分析规则：BFS 遍历关系网络，分析变更影响范围

本体语义：修改某个实体后，通过关系网络推导受影响的所有实体
- 正向关系（source 变更影响 target）：causes, contains, impacts, relates_to
- 反向关系（target 变更影响 source）：depends_on, implements, refines, constrains
- 双向关系：conflicts_with
"""

from collections import deque
from engine.core import Rule
from engine.result import InferenceResult
from models.schema import get_connection
from models.Istaroth import time_filter_sql


class ImpactAnalysisRule(Rule):
    name = "impact_analysis"
    description = "BFS遍历关系网络，分析变更影响范围"

    # 正向关系（source 变更 -> 影响 target）
    FORWARD_TYPES = ["causes", "contains", "impacts", "relates_to"]
    # 反向关系（target 变更 -> 影响 source）
    REVERSE_TYPES = ["depends_on", "implements", "refines", "constrains"]
    # 双向关系
    BIDIRECTIONAL_TYPES = ["conflicts_with"]

    MAX_DEPTH = 5

    def is_applicable(self, entity_ids, db_path=None):
        if not entity_ids:
            return False
        conn = get_connection(db_path)
        placeholders = ",".join("?" * len(entity_ids))
        all_types = self.FORWARD_TYPES + self.REVERSE_TYPES + self.BIDIRECTIONAL_TYPES
        type_placeholders = ",".join("?" * len(all_types))
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM relations r
                WHERE r.type_id IN ({type_placeholders})
                AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
            all_types + list(entity_ids) + list(entity_ids)
        ).fetchone()
        conn.close()
        return row["cnt"] > 0

    def apply(self, entity_ids, db_path=None, include_future=False, include_expired=False):
        conn = get_connection(db_path)
        results = []

        # 时间过滤条件（由 Istaroth 统一生成）
        time_sql, time_params = time_filter_sql("r", include_future, include_expired)

        for eid in entity_ids:
            visited = {eid}
            # BFS queue: (current_entity_id, depth, path_list)
            queue = deque([(eid, 0, [])])

            while queue:
                current, depth, path = queue.popleft()

                if depth >= self.MAX_DEPTH:
                    continue

                # 正向遍历：source -> target
                f_placeholders = ",".join("?" * len(self.FORWARD_TYPES))
                rows = conn.execute(
                    f"""SELECT r.target_id, r.type_id, r.confidence,
                              e.name AS target_name
                       FROM relations r
                       JOIN entities e ON r.target_id = e.id
                       WHERE r.source_id = ?
                         AND r.type_id IN ({f_placeholders})
                         AND e.status = 'active'{time_sql}""",
                    [current] + self.FORWARD_TYPES + time_params
                ).fetchall()

                for row in rows:
                    nid = row["target_id"]
                    if nid not in visited:
                        visited.add(nid)
                        new_path = path + [f"{current} --{row['type_id']}--> {nid}"]
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="impact",
                            source_entity_id=eid,
                            target_entity_id=nid,
                            relation_type=row["type_id"],
                            evidence=" -> ".join(new_path) + " (影响分析-BFS正向)",
                            confidence=row["confidence"] * (0.8 ** depth),
                            depth=depth + 1,
                        ))
                        queue.append((nid, depth + 1, new_path))

                # 反向遍历：target -> source
                r_placeholders = ",".join("?" * len(self.REVERSE_TYPES))
                rows = conn.execute(
                    f"""SELECT r.source_id, r.type_id, r.confidence,
                              e.name AS source_name
                       FROM relations r
                       JOIN entities e ON r.source_id = e.id
                       WHERE r.target_id = ?
                         AND r.type_id IN ({r_placeholders})
                         AND e.status = 'active'{time_sql}""",
                    [current] + self.REVERSE_TYPES + time_params
                ).fetchall()

                for row in rows:
                    nid = row["source_id"]
                    if nid not in visited:
                        visited.add(nid)
                        new_path = path + [f"{current} <--{row['type_id']}-- {nid}"]
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="impact",
                            source_entity_id=eid,
                            target_entity_id=nid,
                            relation_type=row["type_id"],
                            evidence=" -> ".join(new_path) + " (影响分析-BFS反向)",
                            confidence=row["confidence"] * (0.8 ** depth),
                            depth=depth + 1,
                        ))
                        queue.append((nid, depth + 1, new_path))

                # 双向遍历：conflicts_with
                b_placeholders = ",".join("?" * len(self.BIDIRECTIONAL_TYPES))
                rows = conn.execute(
                    f"""SELECT
                              CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END AS other_id,
                              r.type_id, r.confidence,
                              e.name AS other_name
                       FROM relations r
                       JOIN entities e ON
                         (CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END) = e.id
                       WHERE (r.source_id = ? OR r.target_id = ?)
                         AND r.type_id IN ({b_placeholders})
                         AND e.status = 'active'{time_sql}""",
                    [current, current, current, current] + self.BIDIRECTIONAL_TYPES + time_params
                ).fetchall()

                for row in rows:
                    nid = row["other_id"]
                    if nid not in visited:
                        visited.add(nid)
                        new_path = path + [f"{current} <--{row['type_id']}--> {nid}"]
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="impact",
                            source_entity_id=eid,
                            target_entity_id=nid,
                            relation_type=row["type_id"],
                            evidence=" -> ".join(new_path) + " (影响分析-BFS双向)",
                            confidence=row["confidence"] * (0.8 ** depth),
                            depth=depth + 1,
                        ))
                        queue.append((nid, depth + 1, new_path))

        conn.close()
        return results
