"""
Relation CRUD 操作。
"""

from .schema import get_connection


def get_entity_relations(entity_id, relation_types=None, db_path=None):
    """查询某个实体的所有关系（出向 + 入向）。
    
    如果指定 relation_types，只返回指定类型的关系。
    """
    conn = get_connection(db_path)

    # 构建查询
    type_filter = ""
    params = [entity_id, entity_id]
    if relation_types:
        placeholders = ",".join("?" * len(relation_types))
        type_filter = f" AND rt.name IN ({placeholders})"
        params.extend(relation_types)

    sql = f"""
    SELECT r.id, rt.name AS relation_type,
           CASE WHEN r.source_id = ? THEN 'outgoing' ELSE 'incoming' END AS direction,
           e.id AS related_entity_id,
           e.name AS related_entity_name,
           et.name AS related_entity_type,
           r.confidence, r.weight, r.metadata,
           r.source, r.source_doc_id
    FROM relations r
    JOIN relation_types rt ON r.type_id = rt.id
    JOIN entities e ON e.id = CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END
    JOIN entity_types et ON e.type_id = et.id
    WHERE (r.source_id = ? OR r.target_id = ?){type_filter}
    ORDER BY r.confidence DESC
    """

    rows = conn.execute(sql, params * 2).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_outgoing_relations(entity_id, relation_types=None, db_path=None):
    """查询指定实体的出向关系。"""
    conn = get_connection(db_path)
    type_filter = ""
    params = [entity_id]
    if relation_types:
        placeholders = ",".join("?" * len(relation_types))
        type_filter = f" AND rt.name IN ({placeholders})"
        params.extend(relation_types)

    rows = conn.execute(
        f"""
        SELECT r.id, rt.name AS relation_type,
               e.id AS target_entity_id,
               e.name AS target_entity_name,
               et.name AS target_entity_type,
               r.confidence, r.weight, r.metadata
        FROM relations r
        JOIN relation_types rt ON r.type_id = rt.id
        JOIN entities e ON r.target_id = e.id
        JOIN entity_types et ON e.type_id = et.id
        WHERE r.source_id = ?{type_filter}
        ORDER BY r.confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_incoming_relations(entity_id, relation_types=None, db_path=None):
    """查询指定实体的入向关系。"""
    conn = get_connection(db_path)
    type_filter = ""
    params = [entity_id]
    if relation_types:
        placeholders = ",".join("?" * len(relation_types))
        type_filter = f" AND rt.name IN ({placeholders})"
        params.extend(relation_types)

    rows = conn.execute(
        f"""
        SELECT r.id, rt.name AS relation_type,
               e.id AS source_entity_id,
               e.name AS source_entity_name,
               et.name AS source_entity_type,
               r.confidence, r.weight, r.metadata
        FROM relations r
        JOIN relation_types rt ON r.type_id = rt.id
        JOIN entities e ON r.source_id = e.id
        JOIN entity_types et ON e.type_id = et.id
        WHERE r.target_id = ?{type_filter}
        ORDER BY r.confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transitive_relations(entity_id, relation_type="depends_on", max_depth=5, db_path=None):
    """递归查询可传递关系（如 A 依赖 B，B 依赖 C → A 依赖 C）。"""
    conn = get_connection(db_path)
    rows = conn.execute(
        """
        WITH RECURSIVE transitive AS (
            -- 初始：直接关系
            SELECT r.target_id, 1 AS depth
            FROM relations r
            WHERE r.source_id = ? AND r.type_id = ?
            UNION ALL
            -- 递归：多跳关系
            SELECT r.target_id, t.depth + 1
            FROM relations r
            JOIN transitive t ON r.source_id = t.target_id
            WHERE r.type_id = ? AND t.depth < ?
        )
        SELECT DISTINCT e.id AS entity_id, e.name AS entity_name,
               et.name AS entity_type, t.depth
        FROM transitive t
        JOIN entities e ON t.target_id = e.id
        JOIN entity_types et ON e.type_id = et.id
        ORDER BY t.depth
        """,
        (entity_id, relation_type, relation_type, max_depth),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def relation_exists(type_id, source_id, target_id, db_path=None):
    """检查关系是否已存在。"""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT 1 FROM relations WHERE type_id = ? AND source_id = ? AND target_id = ?",
        (type_id, source_id, target_id),
    ).fetchone()
    conn.close()
    return row is not None


def get_relation_by_entities(source_id, target_id, type_id, db_path=None):
    """按源/目标/类型查询关系。"""
    conn = get_connection(db_path)
    row = conn.execute(
        """
        SELECT r.*, rt.name AS relation_type
        FROM relations r
        JOIN relation_types rt ON r.type_id = rt.id
        WHERE r.source_id = ? AND r.target_id = ? AND r.type_id = ?
        """,
        (source_id, target_id, type_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_relation(
    type_id,
    source_id,
    target_id,
    weight=1.0,
    confidence=0.8,
    source="llm",
    source_doc_id=None,
    metadata=None,
    db_path=None,
):
    """创建关系。若已存在则返回已有关系。"""
    import json
    from engine.cache import invalidate_on_relation_change

    existing = get_relation_by_entities(source_id, target_id, type_id, db_path)
    if existing:
        return existing

    relation_id = f"{type_id}:{source_id}->{target_id}"
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO relations (
            id, type_id, source_id, target_id, weight, confidence,
            source, source_doc_id, metadata, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (relation_id, type_id, source_id, target_id, weight, confidence, source, source_doc_id, metadata_json),
    )
    conn.commit()
    conn.close()
    invalidate_on_relation_change(source_id, target_id)
    return get_relation_by_entities(source_id, target_id, type_id, db_path)


def update_relation(relation_id, updates, db_path=None):
    """更新关系部分字段。"""
    import json
    from engine.cache import invalidate_on_relation_change

    allowed = {"weight", "confidence", "metadata"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return None

    if "metadata" in filtered and isinstance(filtered["metadata"], dict):
        filtered["metadata"] = json.dumps(filtered["metadata"], ensure_ascii=False)

    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    set_clause += ", updated_at = datetime('now')"
    values = list(filtered.values())
    values.append(relation_id)

    conn = get_connection(db_path)
    # 先获取 source_id 和 target_id 用于缓存失效
    row = conn.execute("SELECT source_id, target_id FROM relations WHERE id = ?", (relation_id,)).fetchone()
    conn.execute(
        f"UPDATE relations SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()

    if row:
        invalidate_on_relation_change(row["source_id"], row["target_id"])

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT r.*, rt.name AS relation_type FROM relations r "
        "JOIN relation_types rt ON r.type_id = rt.id WHERE r.id = ?",
        (relation_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_relation(relation_id, db_path=None):
    """删除关系。"""
    from engine.cache import invalidate_on_relation_change

    conn = get_connection(db_path)
    row = conn.execute("SELECT source_id, target_id FROM relations WHERE id = ?", (relation_id,)).fetchone()
    cur = conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
    conn.commit()
    conn.close()

    if row:
        invalidate_on_relation_change(row["source_id"], row["target_id"])

    return cur.rowcount > 0
