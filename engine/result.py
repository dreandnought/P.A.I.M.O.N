# engine/result.py
"""推理结果数据结构"""

from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class InferenceResult:
    """单条推理结果"""
    rule_name: str           # 规则名称（如 "transitive_closure"）
    inference_type: str      # 推理类型：dependency / constraint / impact / conflict
    source_entity_id: str    # 推理起点
    target_entity_id: str    # 推理终点
    relation_type: str       # 关系类型
    evidence: str            # 推理证据（如 "A->B->C, transitive closure"）
    confidence: float        # 置信度（规则推导的置信度，非 LLM 猜测）
    depth: int = 1           # 推理深度（几跳）

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "inference_type": self.inference_type,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "relation_type": self.relation_type,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 4),
            "depth": self.depth,
        }


@dataclass
class ReasoningOutput:
    """推理引擎完整输出"""
    entity_ids: list                     # 入口实体 IDs
    inferences: list                     # 所有推理结果 (list[InferenceResult])
    conflicts: list                      # 冲突检测结果 (list[InferenceResult])
    subgraph: dict                       # 推理涉及的子图
    stats: dict = field(default_factory=dict)

    def by_type(self, inference_type: str) -> list:
        """按推理类型筛选结果"""
        return [r for r in self.inferences if r.inference_type == inference_type]

    def by_rule(self, rule_name: str) -> list:
        """按规则名称筛选结果"""
        return [r for r in self.inferences if r.rule_name == rule_name]

    def to_llm_format(self) -> str:
        """将推理结果格式化为 LLM 可读的文本（供阶段 4 融合使用）"""
        lines = []

        # 按推理类型分组
        deps = self.by_type("dependency")
        constraints = self.by_type("constraint")
        impacts = self.by_type("impact")
        conflicts = self.conflicts

        if deps:
            lines.append("### 隐含依赖（规则推理）")
            for r in deps:
                lines.append(
                    f"- **{r.source_entity_id}** -> {r.relation_type} -> "
                    f"**{r.target_entity_id}** (置信度: {r.confidence:.2f}, 深度: {r.depth})\n"
                    f"  证据: {r.evidence} [{r.rule_name}]"
                )

        if constraints:
            lines.append("\n### 约束条件（规则推理）")
            for r in constraints:
                lines.append(
                    f"- **{r.source_entity_id}** constrains **{r.target_entity_id}** "
                    f"(置信度: {r.confidence:.2f})\n"
                    f"  证据: {r.evidence} [{r.rule_name}]"
                )

        if impacts:
            lines.append("\n### 影响范围（规则推理）")
            for r in impacts:
                lines.append(
                    f"- **{r.source_entity_id}** 影响到 **{r.target_entity_id}** "
                    f"(置信度: {r.confidence:.2f}, 深度: {r.depth})\n"
                    f"  证据: {r.evidence} [{r.rule_name}]"
                )

        if conflicts:
            lines.append("\n### ⚠️ 冲突检测结果")
            for r in conflicts:
                lines.append(
                    f"- **冲突**: {r.evidence} (置信度: {r.confidence:.2f}) [{r.rule_name}]"
                )

        if not lines:
            return "（规则引擎未产生推理结果）"

        # 统计信息
        lines.append(f"\n### 推理统计")
        lines.append(f"- 入口实体数: {len(self.entity_ids)}")
        lines.append(f"- 推理结果总数: {len(self.inferences)}")
        lines.append(f"- 冲突检测数: {len(self.conflicts)}")
        lines.append(f"- 子图实体数: {len(self.subgraph.get('entities', []))}")
        lines.append(f"- 子图关系数: {len(self.subgraph.get('relations', []))}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "entity_ids": self.entity_ids,
            "inferences": [r.to_dict() for r in self.inferences],
            "conflicts": [r.to_dict() for r in self.conflicts],
            "subgraph": self.subgraph,
            "stats": self.stats,
        }
