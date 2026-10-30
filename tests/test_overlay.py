from reframe.overlay import make_overlay


def test_overlay_uses_video_margins_and_real_window_dimensions():
    record = {
        "state": "tracking",
        "target_id": 2,
        "sample_rate": 10,
        "lookahead": 2.5,
        "decoder_rtf": 20,
        "behind": 0,
        "target_box": [0.3, 0.1, 0.4, 0.8],
    }
    dimensions = {"w": 600, "h": 1000, "ml": -600, "mr": -600, "mt": 0, "mb": 0}
    text = make_overlay(record, dimensions)
    assert "m -60 100 l 120 100 120 800 -60 800 -60 100" in text
    assert "behind 0" in text
