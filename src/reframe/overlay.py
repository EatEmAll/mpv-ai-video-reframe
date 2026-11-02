"""Map tracked source rectangles into an ASS overlay in window coordinates."""

from itertools import zip_longest

SLASH = chr(92)
GREEN, ORANGE, CYAN = "00FF00", "0080FF", "FFFF00"  # ASS colours are BGR


def _rectangle(box, dimensions, color, confidence=None):
    w, h = dimensions["w"], dimensions["h"]
    sx = w - dimensions["ml"] - dimensions["mr"]
    sy = h - dimensions["mt"] - dimensions["mb"]
    x0, x1 = (round(dimensions["ml"] + box[i] * sx) for i in [0, 2])
    y0, y1 = (round(dimensions["mt"] + box[i] * sy) for i in [1, 3])
    tags = (
        "{"
        + SLASH
        + "an7"
        + SLASH
        + "pos(0,0)"
        + SLASH
        + "p1"
        + SLASH
        + "bord2"
        + SLASH
        + "shad0"
        + SLASH
        + "1a&HFF&"
        + SLASH
        + f"3c&H{color}&}}"
    )
    text = "\n" + tags + f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1} {x0} {y0}"
    if confidence is None or x1 <= 0 or x0 >= w:
        return text
    # Inside the box's top-left corner, kept on screen when the crop cuts the box.
    label_x = min(max(x0, 0) + 6, w - 60)
    label_y = max(y0, 0) + 6
    label = (
        "{"
        + SLASH
        + "an7"
        + SLASH
        + f"pos({label_x},{label_y})"
        + SLASH
        + "fs18"
        + SLASH
        + "bord1"
        + SLASH
        + "shad0"
        + SLASH
        + f"1c&H{color}&"
        + SLASH
        + "3c&H000000&}"
    )
    return text + "\n" + label + f"{confidence:.2f}"


def make_overlay(record, dimensions):
    group = record.get("mode") == "group"
    if group:
        subject = (
            f"group {len(record.get('group_ids') or [])}/"
            f"{record.get('group_candidates', 0)}"
        )
    else:
        subject = f"ID {record['target_id']}"
    hud = (
        f"Reframe: {record['state']} | {subject} | "
        f"{record['sample_rate']:.1f} Hz | lead {record['lookahead']:.2f}s"
        + SLASH
        + "N"
        f"decode {record['decoder_rtf']:.1f}x | behind {record['behind']}"
    )
    text = (
        "{"
        + SLASH
        + "an7"
        + SLASH
        + "pos(12,12)"
        + SLASH
        + "fs18"
        + SLASH
        + "bord1}"
        + hud
    )
    if group:
        # Framed people green, eligible-but-unreachable orange, union cyan.
        for color, boxes, confidences in (
            (GREEN, "group_framed", "group_framed_confidence"),
            (ORANGE, "group_unframed", "group_unframed_confidence"),
        ):
            for box, confidence in zip_longest(
                record.get(boxes) or [], record.get(confidences) or []
            ):
                text += _rectangle(box, dimensions, color, confidence)
        if len(record.get("group_framed") or []) > 1 and record.get("group_box"):
            text += _rectangle(record["group_box"], dimensions, CYAN)
        return text
    box = record.get("target_box")
    if box:
        text += _rectangle(box, dimensions, GREEN, record.get("target_confidence"))
    return text
