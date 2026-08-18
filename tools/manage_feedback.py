"""
MCP Tool: manage_feedback

自监督反馈工作流工具。
让 Agent 可以提交对推理结果的验证反馈，查询反馈统计。

Phase 4 基础版：记录 + 查询，不做自动参数调优。
"""

from typing import Optional

from engine.feedback import submit_feedback, get_feedback_stats, list_recent_feedback


def register(mcp):
    """注册 manage_feedback 工具到 MCP 服务器。"""

    @mcp.tool()
    def manage_feedback(
        action: str = "stats",
        prediction_id: Optional[str] = None,
        status: Optional[str] = None,
        actual_result: Optional[str] = None,
        developer_note: Optional[str] = None,
        pr_id: Optional[str] = None,
        limit: int = 20,
        db_path: Optional[str] = None,
    ) -> dict:
        """**自监督反馈管理工具**：记录和查询推理结果的验证反馈。

        本工具实现了自监督迭代框架的反馈记录环节：
        1. 推理引擎产生预测（parse_prd / reason_ontology）
        2. Agent 在实际开发中验证预测是否正确
        3. 通过本工具提交验证结果
        4. 积累的反馈数据用于未来校准推理规则

        ## Args

        - `action`: 操作类型
          - `"stats"`（默认）：获取反馈统计（准确率、待验证数等）
          - `"submit"`：提交验证反馈
          - `"list"`：列出最近的反馈记录
        - `prediction_id`: 预测 ID（action="submit" 时必填）
        - `status`: 验证结果（action="submit" 时必填）
          - `"confirmed"`：全部推理正确
          - `"rejected"`：全部推理错误
          - `"partial"`：部分正确部分错误
        - `actual_result`: 实际开发结果描述
        - `developer_note`: 开发者备注
        - `pr_id`: 关联的 PR/Commit ID
        - `limit`: 列表条数限制（action="list" 时有效）
        - `db_path`: 可选，Ontology SQLite 数据库路径

        ## Returns

        - action="stats": 反馈统计信息
        - action="submit": 更新后的反馈记录
        - action="list": 最近反馈记录列表
        """
        if action == "stats":
            return get_feedback_stats(db_path)

        elif action == "submit":
            if not prediction_id:
                return {"error": "action='submit' 时需要 prediction_id"}
            if status not in ("confirmed", "rejected", "partial"):
                return {"error": "status 必须为 confirmed/rejected/partial"}
            return submit_feedback(
                prediction_id=prediction_id,
                status=status,
                actual_result=actual_result,
                developer_note=developer_note,
                pr_id=pr_id,
                db_path=db_path,
            )

        elif action == "list":
            return {"feedback": list_recent_feedback(limit, db_path)}

        else:
            return {"error": f"未知 action: {action}，可选: stats, submit, list"}

    return manage_feedback
