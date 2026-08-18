"""
伊斯塔露：本体时序操作中心（Ontology Temporal Operations）

本模块集中所有与"本体中时间关系"相关的操作，是时间维度的单一入口：
- 自然语言时间解析（parse_human_time / extract_time_info）
- SQL 时间过滤（time_filter_sql）
- 当前时间（now_iso）
- 时间字段迁移（migrate_time_fields）
- 关系时间计算（compute_relation_time_from_endpoints）
- 未来实体 / 未来关系查询（get_future_entities / get_future_relations）
- 实体 / 关系转正（activate_entity / activate_relation）
- 引擎规则用的 CTE 时间谓词构造（cte_time_predicates / cte_time_params）

设计原则：
- 默认查询"现在"生效的实关系；通过 include_future / include_expired 显式放宽。
- 到期关系不在物理层删除，而是在 SQL 过滤层被忽略。
- 唯一会"修改"时间字段的写操作是 activate_*（未来 → 实）。
"""

import json
import re
from datetime import datetime, timedelta, timezone

from .schema import get_connection

# ============================================================================
# 基础时间工具
# ============================================================================

# 兼容老代码：本模块内仍以 _now_iso 作为内部名，对外提供 now_iso。
# 下划线版本在文件内别名，避免在多处调用点重写。
_now_iso = now_iso = lambda: datetime.now(timezone.utc).isoformat()


def time_filter_sql(alias, include_future=False, include_expired=False, now=None):
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
        conditions.append(f"({alias}.valid_from IS NULL OR {alias}.valid_from <= ?)")
        params.append(now)
    if not include_expired:
        conditions.append(f"({alias}.valid_until IS NULL OR {alias}.valid_until > ?)")
        params.append(now)
    return " AND " + " AND ".join(conditions), params


# ============================================================================
# 引擎规则辅助：在递归 CTE / 循环谓词中复用时间过滤
# ============================================================================

# 向后兼容：旧代码使用 _time_filter_sql（下划线前缀）。
_time_filter_sql = time_filter_sql


def cte_time_predicates(alias="r", include_future=False, include_expired=False):
    """返回 (future_cond, expired_cond) 两个 SQL 片段（带前导 ' AND '），用于递归 CTE。

    用法示例::

        future_cond, expired_cond = cte_time_predicates("r", inc_f, inc_e)
        sql = f\"\"\"WITH RECURSIVE t AS (
            SELECT ... FROM relations r WHERE ...{future_cond}{expired_cond}
            UNION ALL
            SELECT ... WHERE ...{future_cond}{expired_cond}
        )\"\"\"
    """
    future_cond = "" if include_future else (
        f" AND ({alias}.valid_from IS NULL OR {alias}.valid_from <= ?) "
    )
    expired_cond = "" if include_expired else (
        f" AND ({alias}.valid_until IS NULL OR {alias}.valid_until > ?) "
    )
    return future_cond, expired_cond


def cte_time_params_list(include_future=False, include_expired=False, now=None):
    """配合 cte_time_predicates 使用的 now 参数列表。"""
    now = now or _now_iso()
    result = []
    if not include_future:
        result.append(now)
    if not include_expired:
        result.append(now)
    return result


# ============================================================================
# Schema 迁移：补齐 entities.available_from / relations.valid_from|valid_until
# ============================================================================

def migrate_time_fields(conn):
    """幂等地补齐 entities 与 relations 的时间字段（PRAGMA + ALTER + CREATE INDEX）。"""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(relations)").fetchall()}
        changed = False
        if "valid_from" not in cols:
            conn.execute("ALTER TABLE relations ADD COLUMN valid_from TEXT")
            changed = True
        if "valid_until" not in cols:
            conn.execute("ALTER TABLE relations ADD COLUMN valid_until TEXT")
            changed = True
        if changed:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relations_valid_from ON relations(valid_from)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relations_valid_until ON relations(valid_until)"
            )
            conn.commit()
    except Exception:
        pass

    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()}
        if "available_from" not in cols:
            conn.execute("ALTER TABLE entities ADD COLUMN available_from TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_available_from ON entities(available_from)"
            )
            conn.commit()
    except Exception:
        pass


# ============================================================================
# 自然语言时间解析
# ============================================================================

# 中文数字映射（支持 1-99）
_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_num_to_int(text):
    """将中文数字转为 int，支持 1-99（如 "三"->3, "十五"->15, "二十"->20, "二十三"->23）。
    无法解析时返回 None。
    """
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        rest = text[1:]
        if rest in _CN_DIGITS:
            return 10 + _CN_DIGITS[rest]
        return None
    if "十" in text:
        parts = text.split("十")
        tens = _CN_DIGITS.get(parts[0], 0)
        ones = _CN_DIGITS.get(parts[1], 0) if parts[1] else 0
        return tens * 10 + ones
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


def parse_human_time(text, now=None, tz=None):
    """把人类可读的时间表达解析为 ISO 8601 字符串。

    Args:
        text: 时间表达（如 "3天后"、"三天后"、"2026-08-08"）
        now: 基准时间（datetime），默认当前时间
        tz: 时区（tzinfo），默认 Asia/Shanghai (+8)

    Returns:
        ISO 8601 字符串；无法解析时返回 None
    """
    if not text:
        return None
    text = str(text).strip()
    if not text:
        return None

    if tz is None:
        tz = timezone(timedelta(hours=8))  # Asia/Shanghai
    if now is None:
        now = datetime.now(tz)

    # 1. 绝对 ISO 8601 / 日期时间
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=tz).isoformat()
        except ValueError:
            continue

    # 2. 相对时间：X天后 / X周后 / X月后 / X小时后
    m = re.match(
        r"^\s*(\d+|[零一二两三四五六七八九十]+)\s*(天|日|周|星期|月|年|小时|分钟|秒)后\s*$",
        text,
    )
    if m:
        num = int(m.group(1)) if m.group(1).isdigit() else _cn_num_to_int(m.group(1))
        if num is None:
            return None
        unit = m.group(2)
        delta_map = {
            "天": timedelta(days=num),
            "日": timedelta(days=num),
            "周": timedelta(weeks=num),
            "星期": timedelta(weeks=num),
            "月": timedelta(days=num * 30),
            "年": timedelta(days=num * 365),
            "小时": timedelta(hours=num),
            "分钟": timedelta(minutes=num),
            "秒": timedelta(seconds=num),
        }
        return (now + delta_map[unit]).isoformat()

    # 3. 相对时间：X天前（历史）
    m = re.match(
        r"^\s*(\d+|[零一二两三四五六七八九十]+)\s*(天|日|周|月|年|小时|分钟|秒)前\s*$",
        text,
    )
    if m:
        num = int(m.group(1)) if m.group(1).isdigit() else _cn_num_to_int(m.group(1))
        if num is None:
            return None
        unit = m.group(2)
        delta_map = {
            "天": timedelta(days=num),
            "日": timedelta(days=num),
            "周": timedelta(weeks=num),
            "月": timedelta(days=num * 30),
            "年": timedelta(days=num * 365),
            "小时": timedelta(hours=num),
            "分钟": timedelta(minutes=num),
            "秒": timedelta(seconds=num),
        }
        return (now - delta_map[unit]).isoformat()

    # 4. 常见词
    word_map = {
        "今天": timedelta(days=0),
        "明天": timedelta(days=1),
        "后天": timedelta(days=2),
        "大后天": timedelta(days=3),
        "下周": timedelta(days=7),
        "下周初": timedelta(days=7),
        "下月": timedelta(days=30),
        "明年": timedelta(days=365),
        "昨天": timedelta(days=-1),
        "前天": timedelta(days=-2),
    }
    for word, delta in word_map.items():
        if text.startswith(word):
            return (now + delta).isoformat()

    return None


def extract_time_info(text, now=None):
    """从一段文本中提取时间信息。

    返回 (valid_from, valid_until, matched_text)：
    - valid_from: 解析到的时间（ISO 或 None）
    - valid_until: 未支持（None）
    - matched_text: 匹配到的时间表达片段
    """
    if not text:
        return None, None, None

    dt = parse_human_time(text, now=now)
    if dt:
        return dt, None, text

    m = re.search(
        r"((?:\d+|[零一二两三四五六七八九十]+)\s*(?:天|日|周|月|年|小时)后|"
        r"(?:\d+|[零一二两三四五六七八九十]+)\s*(?:天|日|周|月|年|小时)前|"
        r"明天|后天|下周|下月|今天|昨天|前天)",
        text,
    )
    if m:
        matched = m.group(1)
        dt = parse_human_time(matched, now=now)
        return dt, None, matched

    return None, None, None


# ============================================================================
# 关系 / 实体的"未来态"查询
# ============================================================================

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


# ============================================================================
# 关系时间计算：根据端点实体的 available_from 推导关系的 valid_from
# ============================================================================

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


# ============================================================================
# 转正操作：未来态 -> 实关系
# ============================================================================

def activate_relation(relation_id, db_path=None):
    """将虚关系转正为实关系（valid_from = now, valid_until = NULL）。

    返回更新后的关系 dict，若不存在返回 None。
    """
    # 延迟导入避免循环依赖
    from .relation import update_relation
    return update_relation(
        relation_id,
        {"valid_from": _now_iso(), "valid_until": None},
        db_path,
    )


def activate_entity(entity_id, db_path=None):
    """将未来实体转正（available_from = now），并级联激活其导致的虚关系。

    返回更新后的实体 dict，若不存在返回 None。
    """
    from engine.cache import (
        invalidate_on_entity_change,
        invalidate_on_relation_change,
    )
    from .entity import get_entity, update_entity

    entity = get_entity(entity_id, db_path)
    if not entity:
        return None

    # 1. 置 available_from = now（转正）
    update_entity(entity_id, {"available_from": _now_iso()}, db_path=db_path)
    invalidate_on_entity_change(entity_id)

    # 2. 扫描 metadata 中标记了该实体的虚关系
    conn = get_connection(db_path)
    now = _now_iso()
    candidate_rows = conn.execute(
        "SELECT id, metadata, valid_from FROM relations "
        "WHERE metadata LIKE ?",
        (f"%time_caused_by_entities%",),
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
                conn3 = get_connection(db_path)
                rel_row = conn3.execute(
                    "SELECT source_id, target_id FROM relations WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                conn3.close()
                if rel_row:
                    invalidate_on_relation_change(
                        rel_row["source_id"], rel_row["target_id"]
                    )

    return get_entity(entity_id, db_path)


# ============================================================================
# 模块自测
# ============================================================================

if __name__ == "__main__":
    tests = ["3天后", "2026-08-08", "明天", "下周", "5天后", "1周后", "2026-08-08 10:00:00", "昨天"]
    for t in tests:
        print(f"{t} -> {parse_human_time(t)}")
