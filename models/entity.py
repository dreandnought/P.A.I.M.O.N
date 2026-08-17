"""
Entity CRUD 操作。
"""

import json

from .schema import get_connection
from .relation import _now_iso, activate_relation


def get_entity(entity_id, db_path=None):
    """按 ID 查询实体。"""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT e.*, et.name AS type_name FROM entities e "
        "JOIN entity_types et ON e.type_id = et.id "
        "WHERE e.id = ?",
        (entity_id,),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_entity_by_name(name, db_path=None):
    """按名称查询实体。"""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT e.*, et.name AS type_name FROM entities e "
        "JOIN entity_types et ON e.type_id = et.id "
        "WHERE e.name = ?",
        (name,),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def search_entities(query, limit=10, db_path=None, include_future=False):
    """关键词搜索实体（匹配名称和描述）。

    默认排除未来生效实体（available_from > now）；include_future=True 时返回全部。
    """
    conn = get_connection(db_path)
    like = f"%{query}%"
    now = _now_iso()
    future_filter = "" if include_future else " AND (e.available_from IS NULL OR e.available_from <= ?)"
    params = (like, like) + (() if include_future else (now,)) + (limit,)
    rows = conn.execute(
        "SELECT e.*, et.name AS type_name FROM entities e "
        "JOIN entity_types et ON e.type_id = et.id "
        f"WHERE (e.name LIKE ? OR e.description LIKE ?){future_filter} "
        "ORDER BY e.confidence DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all_entities(db_path=None):
    """列出所有实体（简化版，供调试使用）。"""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT e.id, e.name, et.name AS type_name, e.status "
        "FROM entities e JOIN entity_types et ON e.type_id = et.id "
        "ORDER BY e.type_id, e.name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_entities_by_ids(entity_ids, db_path=None):
    """批量查询实体。"""
    if not entity_ids:
        return []
    conn = get_connection(db_path)
    placeholders = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"SELECT e.*, et.name AS type_name FROM entities e "
        f"JOIN entity_types et ON e.type_id = et.id "
        f"WHERE e.id IN ({placeholders})",
        entity_ids,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def entity_exists(entity_id, db_path=None):
    """检查实体 ID 是否已存在。"""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT 1 FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    conn.close()
    return row is not None


def create_entity(
    entity_id,
    type_id,
    name,
    description=None,
    confidence=0.8,
    source="llm",
    source_doc_id=None,
    available_from=None,
    db_path=None,
):
    """创建实体。"""
    from engine.cache import invalidate_on_entity_change

    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO entities (
            id, type_id, name, description, status, confidence,
            source, source_doc_id, available_from, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (entity_id, type_id, name, description, confidence, source, source_doc_id, available_from),
    )
    conn.commit()
    conn.close()
    invalidate_on_entity_change(entity_id)
    return get_entity(entity_id, db_path)


def update_entity(entity_id, updates, db_path=None):
    """更新实体部分字段。

    updates 为字典，可包含：name, description, type_id, status, confidence, tags 等。
    """
    from engine.cache import invalidate_on_entity_change

    allowed = {"name", "description", "type_id", "status", "confidence", "tags", "properties", "source_ref", "available_from"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return get_entity(entity_id, db_path)

    set_clause = ", ".join(f"{k} = ?" for k in filtered if k != "updated_at")
    # 手动追加 updated_at
    set_clause += ", updated_at = datetime('now')"
    values = [filtered[k] for k in filtered if k != "updated_at"]
    values.append(entity_id)

    conn = get_connection(db_path)
    conn.execute(
        f"UPDATE entities SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()
    invalidate_on_entity_change(entity_id)
    return get_entity(entity_id, db_path)


def delete_entity(entity_id, db_path=None):
    """删除实体及其关联关系。"""
    from engine.cache import invalidate_on_entity_change

    conn = get_connection(db_path)
    # 先找出所有关联实体 ID，用于失效缓存
    related_rows = conn.execute(
        "SELECT DISTINCT source_id FROM relations WHERE target_id = ? "
        "UNION SELECT DISTINCT target_id FROM relations WHERE source_id = ?",
        (entity_id, entity_id)
    ).fetchall()
    conn.execute("DELETE FROM relations WHERE source_id = ? OR target_id = ?", (entity_id, entity_id))
    cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    conn.commit()
    conn.close()
    invalidate_on_entity_change(entity_id)
    for r in related_rows:
        invalidate_on_entity_change(r["source_id"] if "source_id" in r.keys() else r[0])
    return cur.rowcount > 0


def resolve_entity_by_name_or_id(identifier, db_path=None):
    """按 ID 或名称解析实体。"""
    if not identifier:
        return None
    entity = get_entity(identifier, db_path)
    if entity:
        return entity
    return get_entity_by_name(identifier, db_path)


def find_or_create_entity(
    name,
    type_id,
    description=None,
    suggested_id=None,
    confidence=0.8,
    source="llm",
    source_doc_id=None,
    available_from=None,
    db_path=None,
):
    """查找或创建实体。

    优先按 suggested_id 查找；未找到则按 name 查找。
    若均不存在则创建新实体，并处理 suggested_id 冲突（自动加后缀）。
    返回 (entity, created)。
    """
    if suggested_id and entity_exists(suggested_id, db_path):
        return get_entity(suggested_id, db_path), False

    existing = get_entity_by_name(name, db_path)
    if existing:
        return existing, False

    entity_id = suggested_id
    if not entity_id:
        # fallback：用类型前缀 + 名称简单 slug
        import re
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name).strip("_").lower()[:32]
        entity_id = f"{type_id}:{slug}" if type_id else slug

    base_id = entity_id
    counter = 2
    while entity_exists(entity_id, db_path):
        entity_id = f"{base_id}_{counter}"
        counter += 1

    entity = create_entity(
        entity_id=entity_id,
        type_id=type_id,
        name=name,
        description=description,
        confidence=confidence,
        source=source,
        source_doc_id=source_doc_id,
        available_from=available_from,
        db_path=db_path,
    )
    return entity, True


def get_future_entities(db_path=None, limit=100):
    """查询所有未来生效的实体（供规划/预览）。"""
    conn = get_connection(db_path)
    now = _now_iso()
    rows = conn.execute(
        """
        SELECT e.id, e.name, et.name AS type_name, e.description, e.available_from
        FROM entities e
        JOIN entity_types et ON e.type_id = et.id
        WHERE e.available_from IS NOT NULL AND e.available_from > ?
        ORDER BY e.available_from ASC
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def compute_relation_time_from_endpoints(source_id, target_id, db_path=None):
    """根据端点实体的 available_from 计算关系应有的 valid_from。

    返回 (valid_from, caused_by_entity_ids)：
    - 端点中 available_from > now 的，取最大值作为 valid_from
    - caused_by_entity_ids 为这些未来端点 id 列表（供 metadata 标记级联激活）
    - 全部端点已生效则返回 (None, [])
    """
    now = _now_iso()
    future_times = []
    caused_by = []

    for eid in (source_id, target_id):
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT available_from FROM entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        if row and row["available_from"] and row["available_from"] > now:
            future_times.append(row["available_from"])
            caused_by.append(eid)

    if not future_times:
        return None, []
    return max(future_times), caused_by


def activate_entity(entity_id, db_path=None):
    """将未来实体转正（available_from = now），并级联激活其导致的虚关系。

    返回更新后的实体 dict，若不存在返回 None。
    """
    from engine.cache import invalidate_on_entity_change, invalidate_on_relation_change

    entity = get_entity(entity_id, db_path)
    if not entity:
        return None

    # 1. 置 available_from = now（转正）
    update_entity(entity_id, {"available_from": _now_iso()}, db_path=db_path)
    invalidate_on_entity_change(entity_id)

    # 2. 扫描 metadata 中标记了该实体的虚关系
    conn = get_connection(db_path)
    now = _now_iso()
    # LIKE 粗筛 + JSON 精确校验
    candidate_rows = conn.execute(
        "SELECT id, metadata, valid_from FROM relations "
        "WHERE metadata LIKE ?",
        (f'%time_caused_by_entities%',),
    ).fetchall()
    conn.close()

    for row in candidate_rows:
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        caused_ids = meta.get("time_caused_by_entities", [])
        if entity_id not in caused_ids:
            continue

        # 检查所有标记的未来端点是否均已转正
        all_active = True
        for cid in caused_ids:
            conn2 = get_connection(db_path)
            ent_row = conn2.execute(
                "SELECT available_from FROM entities WHERE id = ?", (cid,)
            ).fetchone()
            conn2.close()
            if ent_row and ent_row["available_from"] and ent_row["available_from"] > now:
                all_active = False
                break

        if all_active and row["valid_from"] and row["valid_from"] > now:
            activated = activate_relation(row["id"], db_path=db_path)
            if activated:
                # 获取关系的两端实体用于缓存失效
                conn3 = get_connection(db_path)
                rel_row = conn3.execute(
                    "SELECT source_id, target_id FROM relations WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                conn3.close()
                if rel_row:
                    invalidate_on_relation_change(rel_row["source_id"], rel_row["target_id"])

    return get_entity(entity_id, db_path)
