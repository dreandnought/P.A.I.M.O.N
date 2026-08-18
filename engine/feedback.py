"""
自监督反馈工作流（Phase 4 基础版）

记录和追踪推理结果的验证反馈，为未来的自监督学习奠定基础。

核心流程：
1. 推理引擎产生预测（prediction）
2. Agent/用户在实际开发中验证预测
3. 将验证结果记录为反馈
4. 反馈数据积累后用于校准推理规则参数

Phase 4 实现范围：
- feedback_log 表自动记录每次推理的预测
- 提供 MCP 工具接口让 Agent 提交验证结果
- 反馈统计和准确率查询
- 不做自动参数调优（Phase 5+）
"""

import json
import time
from typing import Optional
from models.schema import get_connection


def log_prediction(
    prediction_id: str,
    entity_ids: list,
    inferences: list,
    source: str = "parse_prd",
    db_path=None,
) -> str:
    """记录一次推理预测，返回 prediction_id。

    Args:
        prediction_id: 预测唯一 ID（由调用方生成，如 UUID）
        entity_ids: 入口实体 ID 列表
        inferences: 推理结果列表
        source: 预测来源（parse_prd / reason_ontology / query_ontology）
        db_path: 数据库路径

    Returns:
        prediction_id
    """
    conn = get_connection(db_path)

    # 确保反馈表存在
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id   TEXT NOT NULL,
            entity_ids      TEXT NOT NULL,
            inferences      TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT 'parse_prd',
            status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending', 'confirmed', 'rejected', 'partial')),
            actual_result   TEXT,
            developer_note  TEXT,
            pr_id           TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            verified_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_pid ON feedback_log(prediction_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_log(status);
    """)

    conn.execute(
        """INSERT INTO feedback_log (prediction_id, entity_ids, inferences, source, status)
           VALUES (?, ?, ?, ?, 'pending')""",
        (prediction_id, json.dumps(entity_ids, ensure_ascii=False),
         json.dumps(inferences, ensure_ascii=False), source),
    )
    conn.commit()
    conn.close()
    return prediction_id


def submit_feedback(
    prediction_id: str,
    status: str,
    actual_result: Optional[str] = None,
    developer_note: Optional[str] = None,
    pr_id: Optional[str] = None,
    db_path=None,
) -> dict:
    """提交对某次预测的验证反馈。

    Args:
        prediction_id: 预测 ID
        status: 验证结果（confirmed=全部正确 / rejected=全部错误 / partial=部分正确）
        actual_result: 实际开发结果描述
        developer_note: 开发者备注
        pr_id: 关联的 PR/Commit ID
        db_path: 数据库路径

    Returns:
        更新后的反馈记录
    """
    conn = get_connection(db_path)

    # 确保表存在
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id   TEXT NOT NULL,
            entity_ids      TEXT NOT NULL,
            inferences      TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT 'parse_prd',
            status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending', 'confirmed', 'rejected', 'partial')),
            actual_result   TEXT,
            developer_note  TEXT,
            pr_id           TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            verified_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_pid ON feedback_log(prediction_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_log(status);
    """)

    conn.execute(
        """UPDATE feedback_log
           SET status = ?, actual_result = ?, developer_note = ?, pr_id = ?, verified_at = datetime('now')
           WHERE prediction_id = ?""",
        (status, actual_result, developer_note, pr_id, prediction_id),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM feedback_log WHERE prediction_id = ?",
        (prediction_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_feedback_stats(db_path=None) -> dict:
    """获取反馈统计信息。"""
    conn = get_connection(db_path)

    # 确保表存在
    try:
        total = conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM feedback_log WHERE status='pending'").fetchone()[0]
        confirmed = conn.execute("SELECT COUNT(*) FROM feedback_log WHERE status='confirmed'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM feedback_log WHERE status='rejected'").fetchone()[0]
        partial = conn.execute("SELECT COUNT(*) FROM feedback_log WHERE status='partial'").fetchone()[0]

        # 按来源分组
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt, "
            "SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) as confirmed, "
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected, "
            "SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) as partial "
            "FROM feedback_log GROUP BY source"
        ).fetchall()

        conn.close()

        verified = confirmed + rejected + partial
        accuracy = (confirmed / verified * 100) if verified > 0 else 0

        return {
            "total": total,
            "pending": pending,
            "confirmed": confirmed,
            "rejected": rejected,
            "partial": partial,
            "accuracy": f"{accuracy:.1f}%",
            "by_source": [dict(r) for r in by_source],
        }
    except Exception:
        conn.close()
        return {
            "total": 0,
            "pending": 0,
            "confirmed": 0,
            "rejected": 0,
            "partial": 0,
            "accuracy": "N/A",
            "by_source": [],
        }


def list_recent_feedback(limit: int = 20, db_path=None) -> list:
    """列出最近的反馈记录。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT prediction_id, entity_ids, source, status, created_at, verified_at, developer_note "
            "FROM feedback_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        conn.close()
        return []
