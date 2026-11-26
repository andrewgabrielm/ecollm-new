"""Per-category scorers for Step 11 benchmark candidate answers.

``score`` is the single public entry point; it dispatches on
``task.category`` and returns a ``ScoreResult`` with correctness and
format axes kept separate per the plan.
"""

from backend.learning.scorers.base import ScoreResult, score, score_batch
from backend.learning.scorers.code import CodeExecutionError

__all__ = [
    "CodeExecutionError",
    "ScoreResult",
    "score",
    "score_batch",
]
