from decimal import Decimal

from home_radar_collector.change_detection import detect_observation_changes
from home_radar_models.enums import ListingEventType


def test_new_listing_is_detected() -> None:
    changes = detect_observation_changes(
        is_new=True,
        previous_price=None,
        current_price=Decimal("3000000"),
    )
    assert [change.event_type for change in changes] == [ListingEventType.NEW]


def test_price_cut_is_detected() -> None:
    changes = detect_observation_changes(
        is_new=False,
        previous_price=Decimal("3100000"),
        current_price=Decimal("2980000"),
    )
    assert [change.event_type for change in changes] == [ListingEventType.PRICE_CUT]


def test_unchanged_price_has_no_observation_event() -> None:
    changes = detect_observation_changes(
        is_new=False,
        previous_price=Decimal("2980000"),
        current_price=Decimal("2980000"),
    )
    assert changes == []


def test_price_increase_is_detected() -> None:
    changes = detect_observation_changes(
        is_new=False,
        previous_price=Decimal("2980000"),
        current_price=Decimal("3100000"),
    )
    assert [change.event_type for change in changes] == [ListingEventType.PRICE_INCREASE]
