from scripts.antibot_detection import summarize_detection
from scripts.browser_handoff import TARGET_URL, pause_reason


def page(detections: list[object]) -> dict:
    return {"url": TARGET_URL, "cards": [{}], "antibot": {"detections": detections}}


def test_loaded_sdk_does_not_block_a_listing_page() -> None:
    evidence = page([{"id": "netease", "evidence": "resource", "requires_human": False}])
    assert pause_reason(evidence, TARGET_URL, 1) is None


def test_visible_challenge_blocks_even_with_listing_cards() -> None:
    evidence = page([{"id": "netease", "evidence": "visible_widget", "requires_human": True}])
    assert "网易易盾" in pause_reason(evidence, TARGET_URL, 1)


def test_diagnostic_state_drops_unknown_providers_and_sensitive_fields() -> None:
    evidence = page(
        [
            {
                "id": "netease",
                "name": "secret",
                "cookie": "private",
                "url": "private",
                "evidence": "visible_widget",
                "requires_human": True,
            },
            {"id": "netease", "evidence": "resource"},
            {"id": "secret"},
            {"id": []},
            None,
        ]
    )
    report = summarize_detection(evidence)
    assert len(report["detections"]) == 1
    assert report["requires_human"] is True
    assert "private" not in str(report)
    assert "secret" not in str(report)


def test_resource_claim_alone_cannot_trigger_active_challenge() -> None:
    evidence = page([{"id": "netease", "evidence": "resource", "requires_human": True}])
    assert summarize_detection(evidence)["requires_human"] is False
