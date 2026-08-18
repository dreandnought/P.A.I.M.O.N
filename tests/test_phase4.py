"""
Phase 4 功能测试

测试新增的：
1. reason_ontology MCP 工具
2. query_ontology 增强推理查询
3. 推理结果缓存
4. 一致性检查器增强（循环依赖检测、置信度异常检测）
5. 自监督反馈工作流

运行: cd CodingOntology && python3 tests/test_phase4.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.schema import init_db, get_connection
from models.entity import create_entity, update_entity, delete_entity
from models.relation import create_relation, delete_relation
from engine.cache import get_cache, invalidate_on_entity_change, invalidate_on_relation_change
from engine.feedback import log_prediction, submit_feedback, get_feedback_stats, list_recent_feedback
from engine.core import ReasoningEngine
from engine.rules.transitive import TransitiveClosureRule
from engine.rules.impact import ImpactAnalysisRule
from engine.rules.conflict import ConflictDetectionRule
from engine.checker import ConsistencyChecker


def _setup_test_db():
    """创建测试数据库"""
    get_cache().invalidate_all()
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(db_path)

    conn = get_connection(db_path)
    entities = [
        ("func:a", "function", "功能A", "功能A描述"),
        ("func:b", "function", "功能B", "功能B描述"),
        ("func:c", "function", "功能C", "功能C描述"),
        ("func:d", "function", "功能D", "功能D描述"),
        ("mod:x", "module", "模块X", "模块X描述"),
    ]
    for eid, etype, name, desc in entities:
        conn.execute(
            "INSERT INTO entities (id, type_id, name, description, confidence, source) VALUES (?, ?, ?, ?, 0.9, 'manual')",
            (eid, etype, name, desc),
        )

    relations = [
        ("depends_on", "func:a", "func:b", 0.95),
        ("depends_on", "func:b", "func:c", 0.90),
        ("depends_on", "func:c", "func:d", 0.85),
        ("contains", "mod:x", "func:a", 1.0),
    ]
    for rtype, src, tgt, conf in relations:
        rid = f"{rtype}:{src}->{tgt}"
        conn.execute(
            "INSERT OR IGNORE INTO relations (id, type_id, source_id, target_id, weight, confidence, source) VALUES (?, ?, ?, ?, 1.0, ?, 'manual')",
            (rid, rtype, src, tgt, conf),
        )

    conn.commit()
    conn.close()
    return db_path


def test_reason_ontology_tool():
    """测试 reason_ontology MCP 工具"""
    print("\n--- test_reason_ontology_tool ---")
    db_path = _setup_test_db()

    try:
        from tools.reason_ontology import _resolve_entity_ids, _build_engine

        # 测试实体 ID 解析
        ids = _resolve_entity_ids(entity_ids=["func:a"], db_path=db_path)
        assert ids == ["func:a"], f"实体ID解析失败: {ids}"

        # 测试名称解析
        ids = _resolve_entity_ids(entity_names=["功能A"], db_path=db_path)
        assert "func:a" in ids, f"名称解析失败: {ids}"

        # 测试关键词搜索
        ids = _resolve_entity_ids(query="功能", db_path=db_path)
        assert len(ids) > 0, "关键词搜索失败"

        # 测试完整推理
        engine = _build_engine(db_path)
        output = engine.run(["func:a"])

        # 应该有传递闭包结果 (a->b->c->d)
        transitive = [r for r in output.inferences if r.rule_name == "transitive_closure"]
        assert len(transitive) > 0, "应产生传递闭包结果"

        # 应该推导出 a -> c (2跳) 和 a -> d (3跳)
        targets = {r.target_entity_id for r in transitive}
        assert "func:c" in targets, "应推导出 a 间接依赖 c"
        assert "func:d" in targets, "应推导出 a 间接依赖 d"

        print(f"✅ reason_ontology: 传递闭包 {len(transitive)} 条, 推理总数 {len(output.inferences)} 条")

    finally:
        os.unlink(db_path)


def test_query_ontology_with_inferences():
    """测试增强版 query_ontology（含推理查询）"""
    print("\n--- test_query_ontology_with_inferences ---")
    db_path = _setup_test_db()

    try:
        from models.entity import get_entity_by_name
        from models.relation import get_entity_relations
        from engine.core import ReasoningEngine
        from engine.rules.transitive import TransitiveClosureRule
        from engine.rules.impact import ImpactAnalysisRule
        from engine.checker import ConsistencyChecker

        # 模拟 query_ontology 的推理增强逻辑
        entity = get_entity_by_name("功能A", db_path)
        assert entity is not None, "实体查找失败"

        relations = get_entity_relations(entity["id"], db_path=db_path)
        assert len(relations) > 0, "应有直接关系"

        # 推理增强
        engine = ReasoningEngine(db_path=db_path)
        engine.register_rule(TransitiveClosureRule())
        engine.register_rule(ImpactAnalysisRule())
        engine.register_checker(ConsistencyChecker())

        output = engine.run([entity["id"]])
        inferences = [r.to_dict() for r in output.inferences]

        assert len(inferences) > len(relations), "推理结果应多于直接关系"

        print(f"✅ query_ontology增强: 直接关系 {len(relations)} 条, 推理结果 {len(inferences)} 条")

    finally:
        os.unlink(db_path)


def test_reasoning_cache():
    """测试推理结果缓存"""
    print("\n--- test_reasoning_cache ---")
    db_path = _setup_test_db()
    cache = get_cache()

    try:
        engine = ReasoningEngine(db_path=db_path)
        engine.register_rule(TransitiveClosureRule())
        engine.register_checker(ConsistencyChecker())

        # 第一次运行（miss）
        cache.invalidate_all()
        output1 = engine.run(["func:a"], use_cache=True)
        stats1 = cache.stats()
        assert stats1["misses"] >= 1, "首次运行应 miss"

        # 第二次运行（hit）
        output2 = engine.run(["func:a"], use_cache=True)
        stats2 = cache.stats()
        assert stats2["hits"] > stats1["hits"], "第二次运行应 hit"

        # 验证缓存结果一致性
        assert len(output1.inferences) == len(output2.inferences), "缓存结果应一致"

        # 测试实体变更失效缓存
        invalidate_on_entity_change("func:b")
        output3 = engine.run(["func:a"], use_cache=True)
        stats3 = cache.stats()
        assert stats3["misses"] > stats2["misses"], "实体变更后应 miss"

        print(f"✅ 缓存: {cache.stats()}")

    finally:
        cache.invalidate_all()
        os.unlink(db_path)


def test_cache_invalidation_on_entity_change():
    """测试实体变更时缓存自动失效"""
    print("\n--- test_cache_invalidation_on_entity_change ---")
    db_path = _setup_test_db()
    cache = get_cache()

    try:
        # 先运行一次建立缓存
        engine = ReasoningEngine(db_path=db_path)
        engine.register_rule(TransitiveClosureRule())
        engine.register_checker(ConsistencyChecker())
        engine.run(["func:a"], use_cache=True)

        assert cache.stats()["cache_size"] > 0, "应有缓存"

        # 通过 create_entity 触发缓存失效
        create_entity("func:e", "function", "功能E", "新功能", db_path=db_path)
        # func:a 的缓存可能还在（因为 create_entity 只失效 func:e 相关的）
        # 但如果更新 func:a，则应失效
        update_entity("func:a", {"description": "更新后的描述"}, db_path=db_path)

        # 重新运行，应 miss
        misses_before = cache.stats()["misses"]
        engine.run(["func:a"], use_cache=True)
        misses_after = cache.stats()["misses"]
        assert misses_after > misses_before, "实体更新后应 miss"

        print(f"✅ 缓存失效: {cache.stats()}")

    finally:
        cache.invalidate_all()
        os.unlink(db_path)


def test_circular_dependency_detection():
    """测试循环依赖检测"""
    print("\n--- test_circular_dependency_detection ---")
    db_path = _setup_test_db()
    get_cache().invalidate_all()

    try:
        conn = get_connection(db_path)
        # 构造循环: d -> a (已有 a->b->c->d)
        conn.execute(
            "INSERT OR IGNORE INTO relations (id, type_id, source_id, target_id, confidence, source) VALUES (?, ?, ?, ?, ?, 'manual')",
            ("depends_on:func:d->func:a", "depends_on", "func:d", "func:a", 0.9),
        )
        conn.commit()
        conn.close()

        get_cache().invalidate_all()

        checker = ConsistencyChecker()
        issues = checker._check_circular_dependencies(["func:a"], db_path)

        assert len(issues) > 0, "应检测到循环依赖"
        assert any(r.relation_type == "circular_dependency" for r in issues), "应有 circular_dependency 类型"

        print(f"✅ 循环依赖检测: {len(issues)} 个问题")

    finally:
        os.unlink(db_path)


def test_confidence_anomaly_detection():
    """测试置信度异常检测"""
    print("\n--- test_confidence_anomaly_detection ---")

    from engine.result import InferenceResult

    inferences = [
        InferenceResult(
            rule_name="test", inference_type="dependency",
            source_entity_id="a", target_entity_id="b",
            relation_type="depends_on", evidence="test",
            confidence=0.1, depth=1,  # 过低置信度
        ),
        InferenceResult(
            rule_name="test", inference_type="dependency",
            source_entity_id="a", target_entity_id="c",
            relation_type="depends_on", evidence="test",
            confidence=0.9, depth=6,  # 深度过深但置信度偏高
        ),
        InferenceResult(
            rule_name="test", inference_type="dependency",
            source_entity_id="a", target_entity_id="d",
            relation_type="depends_on", evidence="test",
            confidence=0.8, depth=1,  # 正常
        ),
    ]

    checker = ConsistencyChecker()
    issues = checker._check_confidence_anomalies(inferences, None)

    assert len(issues) >= 2, f"应检测到 2 个异常，实际 {len(issues)}"
    types = {r.relation_type for r in issues}
    assert "low_confidence" in types, "应检测到低置信度"
    assert "suspicious_depth" in types, "应检测到深度异常"

    print(f"✅ 置信度异常检测: {len(issues)} 个问题")


def test_feedback_workflow():
    """测试自监督反馈工作流"""
    print("\n--- test_feedback_workflow ---")
    db_path = _setup_test_db()

    try:
        # 1. 记录预测
        pred_id = log_prediction(
            "test-pred-001",
            ["func:a", "func:b"],
            [{"rule_name": "transitive_closure", "source_entity_id": "func:a", "target_entity_id": "func:c"}],
            source="test",
            db_path=db_path,
        )
        assert pred_id == "test-pred-001"

        # 2. 查询统计（应有 1 条 pending）
        stats = get_feedback_stats(db_path)
        assert stats["total"] >= 1, f"应有至少 1 条记录，实际 {stats['total']}"
        assert stats["pending"] >= 1, "应有至少 1 条 pending"

        # 3. 提交反馈
        result = submit_feedback(
            "test-pred-001",
            status="confirmed",
            actual_result="推理结果全部正确",
            developer_note="测试确认",
            db_path=db_path,
        )
        assert result["status"] == "confirmed"

        # 4. 查询更新后的统计
        stats = get_feedback_stats(db_path)
        assert stats["confirmed"] >= 1, "应有至少 1 条 confirmed"

        # 5. 列出反馈
        feedback_list = list_recent_feedback(10, db_path)
        assert len(feedback_list) >= 1

        print(f"✅ 反馈工作流: stats={stats}")

    finally:
        os.unlink(db_path)


def test_cache_with_relation_change():
    """测试关系变更时缓存自动失效"""
    print("\n--- test_cache_with_relation_change ---")
    db_path = _setup_test_db()
    cache = get_cache()

    try:
        engine = ReasoningEngine(db_path=db_path)
        engine.register_rule(TransitiveClosureRule())
        engine.register_checker(ConsistencyChecker())

        # 建立缓存
        engine.run(["func:a"], use_cache=True)
        assert cache.stats()["cache_size"] > 0

        # 添加新关系
        create_relation("depends_on", "func:a", "func:d", confidence=0.8, db_path=db_path)

        # 重新运行，应 miss（因为 create_relation 触发了缓存失效）
        misses_before = cache.stats()["misses"]
        engine.run(["func:a"], use_cache=True)
        misses_after = cache.stats()["misses"]
        assert misses_after > misses_before, "关系变更后应 miss"

        print(f"✅ 关系变更缓存失效: {cache.stats()}")

    finally:
        cache.invalidate_all()
        os.unlink(db_path)


# ─── 主入口 ───

if __name__ == "__main__":
    print("=" * 60)
    print("CodingOntology Phase 4 功能测试")
    print("=" * 60)

    tests = [
        test_reason_ontology_tool,
        test_query_ontology_with_inferences,
        test_reasoning_cache,
        test_cache_invalidation_on_entity_change,
        test_circular_dependency_detection,
        test_confidence_anomaly_detection,
        test_feedback_workflow,
        test_cache_with_relation_change,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"总计: {passed} 通过, {failed} 失败, {len(tests)} 总数")
    print(f"{'='*60}")
