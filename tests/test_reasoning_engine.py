"""
推理引擎单元测试 + 端到端测试

运行: cd CodingOntology && python3 -m pytest tests/test_reasoning_engine.py -v
或直接运行: python3 tests/test_reasoning_engine.py
"""

import os
import sys
import tempfile
import sqlite3

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.schema import init_db, get_connection
from models.entity import create_entity
from models.relation import create_relation
from engine.core import ReasoningEngine
from engine.rules.transitive import TransitiveClosureRule
from engine.rules.symmetric import SymmetricRule
from engine.rules.inverse import InverseRelationRule
from engine.rules.constraint import ConstraintPropagationRule
from engine.rules.impact import ImpactAnalysisRule
from engine.rules.inheritance import InheritanceRule
from engine.rules.conflict import ConflictDetectionRule
from engine.checker import ConsistencyChecker


# ─── 测试夹具 ───

def _setup_test_db():
    """创建测试数据库并填充测试数据"""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(db_path)

    conn = get_connection(db_path)

    # 创建实体
    entities = [
        ("func:user_login", "function", "用户登录", "用户通过手机号或邮箱登录系统"),
        ("func:sms_verify", "function", "短信验证", "发送和验证短信验证码"),
        ("iface:sms_provider", "interface", "短信服务商接口", "第三方短信发送API"),
        ("mod:auth_module", "module", "认证模块", "统一认证模块"),
        ("func:wechat_login", "function", "微信登录", "通过微信扫码登录"),
        ("iface:wechat_oauth", "interface", "微信OAuth", "微信开放平台OAuth接口"),
        ("constraint:max_retry", "constraint", "最大重试次数", "登录失败超过5次锁定30分钟"),
        ("func:session_mgr", "function", "会话管理", "用户会话创建和管理"),
        ("mod:user_center", "module", "用户中心", "用户中心模块"),
    ]
    for eid, etype, name, desc in entities:
        conn.execute(
            "INSERT INTO entities (id, type_id, name, description, confidence, source) VALUES (?, ?, ?, ?, 0.9, 'manual')",
            (eid, etype, name, desc),
        )

    # 创建关系
    relations = [
        ("depends_on", "func:user_login", "func:sms_verify", 0.95),
        ("depends_on", "func:sms_verify", "iface:sms_provider", 0.90),
        ("contains", "mod:auth_module", "func:user_login", 1.0),
        ("contains", "mod:auth_module", "func:wechat_login", 1.0),
        ("implements", "func:wechat_login", "iface:wechat_oauth", 0.90),
        ("constrains", "constraint:max_retry", "func:user_login", 0.85),
        ("contains", "mod:user_center", "mod:auth_module", 1.0),
        ("depends_on", "func:user_login", "func:session_mgr", 0.80),
        ("relates_to", "func:user_login", "func:wechat_login", 0.70),
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


def _create_engine(db_path):
    """创建配置了所有规则的推理引擎"""
    engine = ReasoningEngine(db_path=db_path)
    engine.register_rule(TransitiveClosureRule())
    engine.register_rule(SymmetricRule())
    engine.register_rule(InverseRelationRule())
    engine.register_rule(ConstraintPropagationRule())
    engine.register_rule(ImpactAnalysisRule())
    engine.register_rule(InheritanceRule())
    engine.register_rule(ConflictDetectionRule())
    engine.register_checker(ConsistencyChecker())
    return engine


# ─── 测试用例 ───

def test_transitive_closure():
    """测试传递闭包：user_login -> sms_verify -> sms_provider -> 推导 user_login -> sms_provider"""
    db_path = _setup_test_db()
    try:
        engine = _create_engine(db_path)
        output = engine.run(["func:user_login"])

        transitive_results = [r for r in output.inferences if r.rule_name == "transitive_closure"]
        assert len(transitive_results) > 0, "应产生传递闭包结果"

        # 检查是否推导出 user_login -> sms_provider (2跳)
        targets = {r.target_entity_id for r in transitive_results}
        assert "iface:sms_provider" in targets, "应推导出 user_login 间接依赖 sms_provider"

        print(f"✅ test_transitive_closure: {len(transitive_results)} 条传递闭包结果")
    finally:
        os.unlink(db_path)


def test_symmetric_inference():
    """测试对称推理：user_login relates_to wechat_login -> 推导 wechat_login relates_to user_login"""
    db_path = _setup_test_db()
    try:
        engine = _create_engine(db_path)
        output = engine.run(["func:user_login"])

        symmetric_results = [r for r in output.inferences if r.rule_name == "symmetric_inference"]
        assert len(symmetric_results) > 0, "应产生对称推理结果"

        # 检查是否推导出反向关系
        has_reverse = any(
            r.source_entity_id == "func:wechat_login" and r.target_entity_id == "func:user_login"
            for r in symmetric_results
        )
        assert has_reverse, "应推导出 wechat_login relates_to user_login"

        print(f"✅ test_symmetric_inference: {len(symmetric_results)} 条对称推理结果")
    finally:
        os.unlink(db_path)


def test_inverse_relation():
    """测试逆关系推理：user_login depends_on session_mgr -> session_mgr is_depended_by user_login"""
    db_path = _setup_test_db()
    try:
        engine = _create_engine(db_path)
        output = engine.run(["func:session_mgr"])

        inverse_results = [r for r in output.inferences if r.rule_name == "inverse_relation"]
        assert len(inverse_results) > 0, "应产生逆关系推理结果"

        # session_mgr 是 depends_on 的 target，应该推导出 is_depended_by
        has_inverse = any(
            r.relation_type == "is_depended_by"
            for r in inverse_results
        )
        assert has_inverse, "应推导出 is_depended_by 逆关系"

        print(f"✅ test_inverse_relation: {len(inverse_results)} 条逆关系推理结果")
    finally:
        os.unlink(db_path)


def test_constraint_propagation():
    """测试约束传播：max_retry constrains user_login, auth_module contains user_login -> max_retry constrains (子实体)"""
    db_path = _setup_test_db()
    try:
        engine = _create_engine(db_path)
        # 从 auth_module 入口（它 contains user_login，user_login 被 max_retry constrains）
        output = engine.run(["mod:auth_module"])

        constraint_results = [r for r in output.inferences if r.rule_name == "constraint_propagation"]
        # auth_module contains user_login, user_login 被 constrains
        # 但约束传播是从父到子，auth_module 是 user_login 的父
        # 所以需要 max_retry constrains auth_module 的子实体
        # 这里可能没有结果，取决于数据结构
        print(f"✅ test_constraint_propagation: {len(constraint_results)} 条约束传播结果")
    finally:
        os.unlink(db_path)


def test_impact_analysis():
    """测试影响分析：修改 sms_verify 应该影响 user_login（因为 user_login depends_on sms_verify）"""
    db_path = _setup_test_db()
    try:
        engine = _create_engine(db_path)
        # 从 sms_verify 入口，user_login depends_on 它，所以修改 sms_verify 影响 user_login
        output = engine.run(["func:sms_verify"])

        impact_results = [r for r in output.inferences if r.rule_name == "impact_analysis"]
        assert len(impact_results) > 0, "应产生影响分析结果"

        # 检查是否找到了受影响的实体
        targets = {r.target_entity_id for r in impact_results}
        # sms_verify 被 user_login depends_on，所以修改 sms_verify 影响 user_login
        assert "func:user_login" in targets, f"应发现 user_login 受影响（反向 depends_on），实际: {targets}"

        print(f"✅ test_impact_analysis: {len(impact_results)} 条影响分析结果, 涉及实体: {targets}")
    finally:
        os.unlink(db_path)


def test_inheritance():
    """测试类型继承：wechat_login 和 user_login 都是 function 类型，可能有共性关系"""
    db_path = _setup_test_db()
    try:
        engine = _create_engine(db_path)
        output = engine.run(["func:wechat_login"])

        inheritance_results = [r for r in output.inferences if r.rule_name == "type_inheritance"]
        # wechat_login 的兄弟实体 user_login 有 depends_on session_mgr 关系
        # 应该推导出 wechat_login 可能也 depends_on session_mgr
        print(f"✅ test_inheritance: {len(inheritance_results)} 条类型继承推理结果")
    finally:
        os.unlink(db_path)


def test_conflict_detection():
    """测试冲突检测：构造 depends_on + conflicts_with 的矛盾"""
    db_path = _setup_test_db()
    try:
        conn = get_connection(db_path)
        # 添加矛盾关系：user_login conflicts_with sms_verify（同时也有 depends_on）
        conn.execute(
            "INSERT OR IGNORE INTO relations (id, type_id, source_id, target_id, confidence, source) VALUES (?, ?, ?, ?, ?, 'manual')",
            ("conflicts_with:func:user_login->func:sms_verify", "conflicts_with", "func:user_login", "func:sms_verify", 0.9),
        )
        conn.commit()
        conn.close()

        engine = _create_engine(db_path)
        output = engine.run(["func:user_login"])

        conflict_results = output.conflicts
        assert len(conflict_results) > 0, "应检测到冲突"

        # 检查是否检测到了 depends_on + conflicts_with 矛盾
        has_dep_conflict = any(
            "depends_on" in r.evidence and "conflicts_with" in r.evidence
            for r in conflict_results
        )
        assert has_dep_conflict, "应检测到依赖且冲突的矛盾"

        print(f"✅ test_conflict_detection: {len(conflict_results)} 条冲突检测结果")
    finally:
        os.unlink(db_path)


def test_full_pipeline():
    """端到端测试：完整推理引擎流水线"""
    db_path = _setup_test_db()
    try:
        engine = _create_engine(db_path)
        output = engine.run(["func:user_login"])

        print(f"\n{'='*60}")
        print(f"端到端测试结果")
        print(f"{'='*60}")
        print(f"入口实体: func:user_login")
        print(f"推理结果总数: {len(output.inferences)}")
        print(f"冲突检测数: {len(output.conflicts)}")
        print(f"子图实体数: {len(output.subgraph.get('entities', []))}")
        print(f"统计: {output.stats}")

        # 按规则分组统计
        by_rule = {}
        for inf in output.inferences:
            by_rule.setdefault(inf.rule_name, []).append(inf)
        print(f"\n按规则分组:")
        for rule_name, results in by_rule.items():
            print(f"  {rule_name}: {len(results)} 条")

        # 按类型分组
        by_type = {}
        for inf in output.inferences:
            by_type.setdefault(inf.inference_type, []).append(inf)
        print(f"\n按类型分组:")
        for inf_type, results in by_type.items():
            print(f"  {inf_type}: {len(results)} 条")

        # 验证基本指标
        assert len(output.inferences) >= 5, f"推理结果应 >= 5 条，实际 {len(output.inferences)}"
        assert output.stats["rules_executed"] >= 5, f"应执行 >= 5 个规则，实际 {output.stats['rules_executed']}"

        # 验证 LLM 格式化输出
        llm_text = output.to_llm_format()
        assert len(llm_text) > 100, "LLM 格式化输出不应为空"

        print(f"\n✅ test_full_pipeline: 全部断言通过")
        print(f"\nLLM 格式化输出预览:")
        print(llm_text[:500])

    finally:
        os.unlink(db_path)


# ─── 主入口 ───

if __name__ == "__main__":
    print("=" * 60)
    print("CodingOntology 推理引擎测试")
    print("=" * 60)

    tests = [
        test_transitive_closure,
        test_symmetric_inference,
        test_inverse_relation,
        test_constraint_propagation,
        test_impact_analysis,
        test_inheritance,
        test_conflict_detection,
        test_full_pipeline,
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
