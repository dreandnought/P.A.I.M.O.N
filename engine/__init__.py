# engine/__init__.py
"""CodingOntology 推理引擎 - 基于规则的推理引擎"""

from .core import ReasoningEngine, Rule
from .result import InferenceResult, ReasoningOutput

__all__ = ["ReasoningEngine", "Rule", "InferenceResult", "ReasoningOutput"]
