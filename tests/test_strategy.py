import pytest

from aegaeon.protocol.schemas import StrategyName
from aegaeon.strategy.router import StrategyRouter


def test_auto_chooses_single_model_strategy() -> None:
    decision = StrategyRouter().choose(StrategyName.AUTO, workers=[])
    assert decision.strategy == StrategyName.SINGLE_MODEL
    assert "Demo execution" in decision.reason


def test_unavailable_strategy_has_explanation() -> None:
    with pytest.raises(ValueError, match="multiple compatible"):
        StrategyRouter().choose(StrategyName.SWARM, workers=[])
