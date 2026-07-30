# engine/rules/__init__.py
"""推理规则集合"""

from .transitive import TransitiveClosureRule
from .symmetric import SymmetricRule
from .inverse import InverseRelationRule
from .constraint import ConstraintPropagationRule
from .impact import ImpactAnalysisRule
from .inheritance import InheritanceRule
from .conflict import ConflictDetectionRule

__all__ = [
    "TransitiveClosureRule",
    "SymmetricRule",
    "InverseRelationRule",
    "ConstraintPropagationRule",
    "ImpactAnalysisRule",
    "InheritanceRule",
    "ConflictDetectionRule",
]
