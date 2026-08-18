"""
时间相关关系（临时关系/虚关系）测试

测试内容：
1. 创建未来关系（valid_from 设为未来时间，默认查询不返回）
2. 创建当前关系（默认查询返回）
3. 未来关系转正（activate_relation 后默认查询返回）
4. 默认过滤（include_future=False 时虚关系被过滤）
5. 显式包含（include_future=True 时虚关系返回）
6. 推理过滤（推理引擎默认不把虚关系纳入，include_future=True 时纳入）
7. 过期关系（valid_until 过去后默认过滤）
8. 迁移（旧库升级后字段存在且旧数据兼容）
9. 缓存隔离（不同时间过滤参数不串缓存）

运行: cd CodingOntology && python3 tests/test_temporal_relations.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from models.schema import init_db, get_connection
from models.entity import create_entity
from models.relation import (
    create_relation, get_entity_relations, get_outgoing_relations,
    activate_relation, get_future_relations,
)
from engine.core import ReasoningEngine
from engine.cache import get_cache
from tools.reason_ontology import _build_engine


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _future(days=3):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past(days=3):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _setup_test_db():
    """创建含 3 个实体的临时测试库。"""
    db = tempfile.mktemp(suffix=".db")
    init_db(db)
    for eid in ["a", "b", "c"]:
        create_entity(eid, "module", f"模块{eid.upper()}", db_path=db)
    return db


def test_create_future_relation_default_filtered():
    """创建未来关系，默认查询不返回。"""
    print("\n--- test_create_future_relation_default_filtered ---")
    db = _setup_test_db()
    try:
        create_relation("relates_to", "a", "c", db_path=db, valid_from=_future())
        rels = get_entity_relations("a", db_path=db)
        assert len(rels) == 0, f"默认应过滤未来关系, 实际 {len(rels)}"
        print(f"✅ 默认过滤未来关系: {len(rels)} 条")
    finally:
        os.unlink(db)


def test_create_current_relation_returned():
    """创建当前关系，默认查询返回。"""
    print("\n--- test_create_current_relation_returned ---")
    db = _setup_test_db()
    try:
        create_relation("depends_on", "a", "b", db_path=db)
        rels = get_entity_relations("a", db_path=db)
        assert len(rels) == 1, f"当前关系应返回, 实际 {len(rels)}"
        print(f"✅ 当前关系默认返回: {len(rels)} 条")
    finally:
        os.unlink(db)


def test_activate_future_relation():
    """未来关系转正后默认查询返回。"""
    print("\n--- test_activate_future_relation ---")
    db = _setup_test_db()
    try:
        r = create_relation("relates_to", "a", "c", db_path=db, valid_from=_future())
        assert len(get_entity_relations("a", db_path=db)) == 0, "转正前应过滤"
        new = activate_relation(r["id"], db_path=db)
        assert new["valid_from"] is not None
        rels = get_entity_relations("a", db_path=db)
        assert len(rels) == 1, f"转正后应返回, 实际 {len(rels)}"
        print(f"✅ 转正后默认返回: {len(rels)} 条")
    finally:
        os.unlink(db)


def test_include_future_flag():
    """include_future=True 时虚关系返回。"""
    print("\n--- test_include_future_flag ---")
    db = _setup_test_db()
    try:
        create_relation("depends_on", "a", "b", db_path=db)
        create_relation("relates_to", "a", "c", db_path=db, valid_from=_future())
        # 默认
        rels = get_entity_relations("a", db_path=db)
        assert len(rels) == 1
        # include_future
        rels2 = get_entity_relations("a", db_path=db, include_future=True)
        assert len(rels2) == 2, f"include_future 应返回 2 条, 实际 {len(rels2)}"
        print(f"✅ include_future 返回 {len(rels2)} 条 (默认 {len(rels)} 条)")
    finally:
        os.unlink(db)


def test_expired_relation_filtered():
    """过期关系默认过滤。"""
    print("\n--- test_expired_relation_filtered ---")
    db = _setup_test_db()
    try:
        create_relation("relates_to", "a", "c", db_path=db, valid_until=_past())
        rels = get_entity_relations("a", db_path=db)
        assert len(rels) == 0, f"过期关系应过滤, 实际 {len(rels)}"
        rels2 = get_entity_relations("a", db_path=db, include_expired=True)
        assert len(rels2) == 1, f"include_expired 应返回, 实际 {len(rels2)}"
        print(f"✅ 过期关系默认过滤, include_expired 返回 {len(rels2)} 条")
    finally:
        os.unlink(db)


def test_reasoning_engine_filters_future():
    """推理引擎默认排除虚关系，include_future=True 时纳入。"""
    print("\n--- test_reasoning_engine_filters_future ---")
    db = _setup_test_db()
    try:
        create_relation("depends_on", "a", "b", db_path=db)
        create_relation("relates_to", "a", "c", db_path=db, valid_from=_future())
        get_cache().invalidate_all()

        # 默认推理
        engine = _build_engine(db, include_future=False, include_expired=False)
        out = engine.run(["a"])
        dep_types = sorted(set(r.relation_type for r in out.inferences))
        assert "relates_to" not in dep_types, f"默认推理不应包含虚关系: {dep_types}"
        assert "depends_on" in dep_types, f"实关系应被推理: {dep_types}"

        # include_future 推理
        engine2 = _build_engine(db, include_future=True, include_expired=False)
        out2 = engine2.run(["a"])
        dep_types2 = sorted(set(r.relation_type for r in out2.inferences))
        assert "relates_to" in dep_types2, f"include_future 推理应包含虚关系: {dep_types2}"

        print(f"✅ 默认推理排除虚关系 ({dep_types}), include_future 纳入 ({dep_types2})")
    finally:
        os.unlink(db)


def test_cache_isolation_by_time_filter():
    """不同时间过滤参数不串缓存。"""
    print("\n--- test_cache_isolation_by_time_filter ---")
    db = _setup_test_db()
    try:
        create_relation("depends_on", "a", "b", db_path=db)
        create_relation("relates_to", "a", "c", db_path=db, valid_from=_future())
        get_cache().invalidate_all()

        # 默认引擎
        e1 = _build_engine(db, include_future=False, include_expired=False)
        o1 = e1.run(["a"])
        # include_future 引擎
        e2 = _build_engine(db, include_future=True, include_expired=False)
        o2 = e2.run(["a"])

        n1 = len(o1.inferences)
        n2 = len(o2.inferences)
        assert n1 != n2, f"不同时间过滤应有不同推理结果: {n1} vs {n2}"

        # 再次运行验证缓存隔离
        o1b = e1.run(["a"])
        o2b = e2.run(["a"])
        assert len(o1b.inferences) == n1
        assert len(o2b.inferences) == n2
        print(f"✅ 缓存隔离: 默认 {n1} 条, include_future {n2} 条 (互不串扰)")
    finally:
        os.unlink(db)


def test_get_future_relations_list():
    """get_future_relations 只返回未来关系。"""
    print("\n--- test_get_future_relations_list ---")
    db = _setup_test_db()
    try:
        create_relation("depends_on", "a", "b", db_path=db)  # 非未来
        r = create_relation("relates_to", "a", "c", db_path=db, valid_from=_future())
        future = get_future_relations(db_path=db)
        assert len(future) == 1, f"应只有 1 条未来关系, 实际 {len(future)}"
        assert future[0]["id"] == r["id"]
        print(f"✅ get_future_relations 返回 {len(future)} 条未来关系")
    finally:
        os.unlink(db)


def test_migration_old_data_compatible():
    """旧库升级后字段存在且旧数据默认视为当前生效。"""
    print("\n--- test_migration_old_data_compatible ---")
    db = _setup_test_db()
    try:
        # 模拟旧数据：直接插入无时间字段的关系（valid_from 为 NULL）
        conn = get_connection(db)
        conn.execute(
            "INSERT OR IGNORE INTO relations (id, type_id, source_id, target_id, confidence, source) "
            "VALUES ('depends_on:a->b', 'depends_on', 'a', 'b', 0.9, 'manual')"
        )
        conn.commit()
        conn.close()

        # 列存在
        conn = get_connection(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(relations)").fetchall()}
        conn.close()
        assert "valid_from" in cols and "valid_until" in cols

        # 旧数据（NULL 时间）默认返回
        rels = get_entity_relations("a", db_path=db)
        assert len(rels) == 1, f"旧数据应视为当前生效, 实际 {len(rels)}"
        print(f"✅ 旧库兼容: 字段存在, 旧数据默认返回 {len(rels)} 条")
    finally:
        os.unlink(db)


if __name__ == "__main__":
    tests = [
        test_create_future_relation_default_filtered,
        test_create_current_relation_returned,
        test_activate_future_relation,
        test_include_future_flag,
        test_expired_relation_filtered,
        test_reasoning_engine_filters_future,
        test_cache_isolation_by_time_filter,
        test_get_future_relations_list,
        test_migration_old_data_compatible,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'='*60}")
    print(f"总计: {passed} 通过, {failed} 失败, {len(tests)} 总数")
    print(f"{'='*60}")
    sys.exit(1 if failed else 0)