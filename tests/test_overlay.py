from reframe.overlay import CYAN, GREEN, ORANGE, SLASH, make_overlay


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


BASE = {
    "sample_rate": 10,
    "lookahead": 2.5,
    "decoder_rtf": 20,
    "behind": 0,
}
WINDOW = {"w": 600, "h": 1000, "ml": 0, "mr": 0, "mt": 0, "mb": 0}


def labels(text):
    # Label events are the lines after the HUD that are not drawings.
    return [line for line in text.split("\n")[1:] if SLASH + "p1" not in line]


def test_follow_box_shows_its_confidence_inside_the_corner():
    record = {
        **BASE,
        "state": "tracking",
        "target_id": 2,
        "target_box": [0.5, 0.1, 0.8, 0.9],
        "target_confidence": 0.8712,
    }
    [label] = labels(make_overlay(record, WINDOW))
    assert label.endswith("}0.87")
    assert SLASH + "pos(306,106)" in label


def test_box_without_a_confidence_has_no_label():
    record = {
        **BASE,
        "state": "buffering",
        "target_id": None,
        "target_box": [0.5, 0.1, 0.8, 0.9],
    }
    assert labels(make_overlay(record, WINDOW)) == []


def test_group_labels_match_their_boxes_and_colours():
    record = {
        **BASE,
        "mode": "group",
        "state": "group",
        "group_ids": [1, 2],
        "group_candidates": 3,
        "group_framed": [[0.1, 0.1, 0.2, 0.9], [0.3, 0.1, 0.4, 0.9]],
        "group_framed_confidence": [0.91, 0.62],
        "group_unframed": [[0.7, 0.1, 0.9, 0.9]],
        "group_unframed_confidence": [0.55],
        "group_box": [0.1, 0.1, 0.4, 0.9],
    }
    found = labels(make_overlay(record, WINDOW))
    assert [line[-4:] for line in found] == ["0.91", "0.62", "0.55"]
    assert all(GREEN in line for line in found[:2]) and ORANGE in found[2]
    # The cyan union is not a detection, so it gets no label.
    assert not any(CYAN in line for line in found)


def test_label_stays_on_screen_when_the_crop_cuts_the_box():
    record = {
        **BASE,
        "state": "tracking",
        "target_id": 2,
        "target_confidence": 0.5,
    }
    wide = {**WINDOW, "ml": -600, "mr": -600}
    cut_left = {**record, "target_box": [0.1, 0.0, 0.4, 0.9]}
    assert SLASH + "pos(6,6)" in labels(make_overlay(cut_left, wide))[0]
    cut_right = {**record, "target_box": [0.66, 0.0, 0.9, 0.9]}
    assert SLASH + "pos(540,6)" in labels(make_overlay(cut_right, wide))[0]
    outside = {**record, "target_box": [0.0, 0.0, 0.2, 0.9]}
    assert labels(make_overlay(outside, wide)) == []
