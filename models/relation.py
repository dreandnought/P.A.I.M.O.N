"""
Relation CRUD 操作。

支持时间有效关系（虚关系）：每条关系可带 valid_from / valid_until 时间窗口。
- 默认查询只返回当前已生效的实关系（old 数据 = 永久有效）
- 通过 include_future / include_expired 可显式包含未来/过期关系
"""

from datetime import datetime, timezone

from .schema import get_connection


def _now_iso():
    """当前 UTC ISO 8601 时间字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _time_filter_sql(alias, include_future=False, include_expired=False, now=None):
    """生成关系时间过滤的 SQL 片段。

    Args:
        alias: relations 表的别名（如 'r'）
        include_future: 是否包含未来生效的虚关系
        include_expired: 是否包含已过期关系
        now: 当前时间（ISO 8601），默认用当前 UTC 时间

    Returns:
        (sql_fragment, params)：sql_fragment 以 ' AND ' 开头；若都不需要过滤则返回 ('', [])。
    """
    now = now or _now_iso()
    if include_future and include_expired:
        return "", []
    conditions = []
    params = []
    if not include_future:
        # 排除未来关系：valid_from IS NULL 或 valid_from <= now
        conditions.append(f"({alias}.valid_from IS NULL OR {alias}.valid_from <= ?)")
        params.append(now)
    if not include_expired:
        # 排除已过期：valid_until IS NULL 或 valid_until > now
        conditions.append(f"({alias}.valid_until IS NULL OR {alias}.valid_until > ?)")
        params.append(now)
    return " AND " + " AND ".join(conditions), params


def get_entity_relations(entity_id, relation_types=None, db_path=None,
                         include_future=False, include_expired=False):
    """查询某个实体的所有关系（出向 + 入向）。
    
    如果指定 relation_types，只返回指定类型的关系。
    默认只返回当前已生效的实关系；include_future/include_expired 可显式包含虚关系。
    """
    conn = get_connection(db_path)

    # 构建查询
    type_filter = ""
    type_params = []
    if relation_types:
        placeholders = ",".join("?" * len(relation_types))
        type_filter = f" AND rt.name IN ({placeholders})"
        type_params.extend(relation_types)

    # 时间过滤
    time_filter, time_params = _time_filter_sql("r", include_future, include_expired)

    sql = f"""
    SELECT r.id, rt.name AS relation_type,
           CASE WHEN r.source_id = ? THEN 'outgoing' ELSE 'incoming' END AS direction,
           e.id AS related_entity_id,
           e.name AS related_entity_name,
           et.name AS related_entity_type,
           r.confidence, r.weight, r.metadata,
           r.source, r.source_doc_id,
           r.valid_from, r.valid_until
    FROM relations r
    JOIN relation_types rt ON r.type_id = rt.id
    JOIN entities e ON e.id = CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END
    JOIN entity_types et ON e.type_id = et.id
    WHERE (r.source_id = ? OR r.target_id = ?){type_filter}{time_filter}
    ORDER BY r.confidence DESC
    """

    # 参数顺序：CASE WHEN source_id(?) + CASE WHEN source_id(?) + WHERE source_id(?) + WHERE target_id(?) + type + time
    params = [entity_id, entity_id, entity_id, entity_id] + type_params + time_params

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_outgoing_relations(entity_id, relation_types=None, db_path=None,
                           include_future=False, include_expired=False):
    """查询指定实体的出向关系。"""
    conn = get_connection(db_path)
    type_filter = ""
    params = [entity_id]
    if relation_types:
        placeholders = ",".join("?" * len(relation_types))
        type_filter = f" AND rt.name IN ({placeholders})"
        params.extend(relation_types)

    time_filter, time_params = _time_filter_sql("r", include_future, include_expired)
    params.extend(time_params)

    rows = conn.execute(
        f"""
        SELECT r.id, rt.name AS relation_type,
               e.id AS target_entity_id,
               e.name AS target_entity_name,
               et.name AS target_entity_type,
               r.confidence, r.weight, r.metadata,
               r.valid_from, r.valid_until
        FROM relations r
        JOIN relation_types rt ON r.type_id = rt.id
        JOIN entities e ON r.target_id = e.id
        JOIN entity_types et ON e.type_id = et.id
        WHERE r.source_id = ?{type_filter}{time_filter}
        ORDER BY r.confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_incoming_relations(entity_id, relation_types=None, db_path=None,
                           include_future=False, include_expired=False):
    """查询指定实体的入向关系。"""
    conn = get_connection(db_path)
    type_filter = ""
    params = [entity_id]
    if relation_types:
        placeholders = ",".join("?" * len(relation_types))
        type_filter = f" AND rt.name IN ({placeholders})"
        params.extend(relation_types)

    time_filter, time_params = _time_filter_sql("r", include_future, include_expired)
    params.extend(time_params)

    rows = conn.execute(
        f"""
        SELECT r.id, rt.name AS relation_type,
               e.id AS source_entity_id,
               e.name AS source_entity_name,
               et.name AS source_entity_type,
               r.confidence, r.weight, r.metadata,
               r.valid_from, r.valid_until
        FROM relations r
        JOIN relation_types rt ON r.type_id = rt.id
        JOIN entities e ON r.source_id = e.id
        JOIN entity_types et ON e.type_id = et.id
        WHERE r.target_id = ?{type_filter}{time_filter}
        ORDER BY r.confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transitive_relations(entity_id, relation_type="depends_on", max_depth=5, db_path=None,
                             include_future=False, include_expired=False):
    """递归查询可传递关系（如 A 依赖 B，B 依赖 C → A 依赖 C）。"""
    conn = get_connection(db_path)
    now = _now_iso()
    # 递归 CTE 中过滤：初始与递归步都排除未来/过期关系
    future_cond = "" if include_future else " AND (r.valid_from IS NULL OR r.valid_from <= ?) "
    expired_cond = "" if include_expired else " AND (r.valid_until IS NULL OR r.valid_until > ?) "
    params = [entity_id, relation_type]
    if not include_future:
        params.append(now)
    if not include_expired:
        params.append(now)
    params += [relation_type, max_depth]
    if not include_future:
        params.append(now)
    if not include_expired:
        params.append(now)

    rows = conn.execute(
        f"""
        WITH RECURSIVE transitive AS (
            -- 初始：直接关系
            SELECT r.target_id, 1 AS depth
            FROM relations r
            WHERE r.source_id = ? AND r.type_id = ?{future_cond}{expired_cond}
            UNION ALL
            -- 递归：多跳关系
            SELECT r.target_id, t.depth + 1
            FROM relations r
            JOIN transitive t ON r.source_id = t.target_id
            WHERE r.type_id = ? AND t.depth < ?{future_cond}{expired_cond}
        )
        SELECT DISTINCT e.id AS entity_id, e.name AS entity_name,
               et.name AS entity_type, t.depth
        FROM transitive t
        JOIN entities e ON t.target_id = e.id
        JOIN entity_types et ON e.type_id = et.id
        ORDER BY t.depth
        """,
        params,
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
    valid_from=None,
    valid_until=None,
):
    """创建关系。若已存在则返回已有关系。

    Args:
        valid_from: 生效时间（ISO 8601），None = 立即生效
        valid_until: 失效时间（ISO 8601），None = 永久有效
    """
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
            source, source_doc_id, metadata, valid_from, valid_until,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (relation_id, type_id, source_id, target_id, weight, confidence, source, source_doc_id, metadata_json, valid_from, valid_until),
    )
    conn.commit()
    conn.close()
    invalidate_on_relation_change(source_id, target_id)
    return get_relation_by_entities(source_id, target_id, type_id, db_path)


def update_relation(relation_id, updates, db_path=None):
    """更新关系部分字段。"""
    import json
    from engine.cache import invalidate_on_relation_change

    allowed = {"weight", "confidence", "metadata", "valid_from", "valid_until"}
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


def activate_relation(relation_id, db_path=None):
    """将虚关系转正为实关系（valid_from = now, valid_until = NULL）。

    返回更新后的关系 dict，若不存在返回 None。
    """
    return update_relation(
        relation_id,
        {"valid_from": _now_iso(), "valid_until": None},
        db_path,
    )


def get_future_relations(db_path=None, limit=100):
    """查询所有未来生效的虚关系（供规划/预览）。"""
    conn = get_connection(db_path)
    now = _now_iso()
    rows = conn.execute(
        """
        SELECT r.id, rt.name AS relation_type,
               e1.id AS source_id, e1.name AS source_name,
               e2.id AS target_id, e2.name AS target_name,
               r.valid_from, r.valid_until, r.confidence, r.metadata
        FROM relations r
        JOIN relation_types rt ON r.type_id = rt.id
        JOIN entities e1 ON r.source_id = e1.id
        JOIN entities e2 ON r.target_id = e2.id
        WHERE r.valid_from IS NOT NULL AND r.valid_from > ?
        ORDER BY r.valid_from ASC
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
