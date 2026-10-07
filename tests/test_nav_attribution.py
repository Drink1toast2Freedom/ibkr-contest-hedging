from datetime import timedelta
from decimal import Decimal

import pytest

from hedge.models import Execution, Position
from hedge.valuation import NAVTracker


def test_intervening_fill_does_not_spend_option_premium_as_nav_loss(manifest, snapshot, quotes, now):
    tracker = NAVTracker(manifest)
    assert tracker.estimate(snapshot, quotes, manifest.contract_map) == 1000000
    price = Decimal(str(quotes[103].ask))
    e = Execution(
        exec_id="fill.01",
        order_id=1,
        account=manifest.account,
        con_id=103,
        signed_quantity=1,
        price=price,
        at=now + timedelta(seconds=1),
    )
    tracker.execution(e)
    changed = snapshot.model_copy(
        update={
            "at": now + timedelta(seconds=2),
            "positions": (*snapshot.positions, Position(con_id=103, quantity=1)),
        }
    )
    expected_loss = (quotes[103].ask - quotes[103].mid) * 100 + 1
    assert tracker.estimate(changed, quotes, manifest.contract_map) == pytest.approx(1000000 - expected_loss)
    tracker.fee(e.exec_id, "1.20")
    assert tracker.estimate(changed, quotes, manifest.contract_map) == pytest.approx(
        1000000 - expected_loss - 0.2
    )
    updated = changed.model_copy(
        update={"nav": Decimal(str(1000000 - expected_loss - 0.2)), "account_at": now + timedelta(seconds=3)}
    )
    assert tracker.estimate(updated, quotes, manifest.contract_map) == pytest.approx(
        1000000 - expected_loss - 0.2
    )


def test_inventory_change_without_fill_cannot_authorize_score_control(manifest, snapshot, quotes):
    tracker = NAVTracker(manifest)
    tracker.estimate(snapshot, quotes, manifest.contract_map)
    changed = snapshot.model_copy(
        update={"positions": (*snapshot.positions, Position(con_id=10, quantity=100))}
    )
    with pytest.raises(ValueError, match="attribution"):
        tracker.estimate(changed, quotes, manifest.contract_map)
