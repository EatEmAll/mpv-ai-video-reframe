"""Map tracked source rectangles into an ASS overlay in window coordinates."""

SLASH = chr(92)
GREEN, ORANGE, CYAN = "00FF00", "0080FF", "FFFF00"  # ASS colours are BGR


def _rectangle(box, dimensions, color):
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
    return "\n" + tags + f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1} {x0} {y0}"


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
        for box in record.get("group_framed") or []:
            text += _rectangle(box, dimensions, GREEN)
        for box in record.get("group_unframed") or []:
            text += _rectangle(box, dimensions, ORANGE)
        if len(record.get("group_framed") or []) > 1 and record.get("group_box"):
            text += _rectangle(record["group_box"], dimensions, CYAN)
        return text
    box = record.get("target_box")
    if box:
        text += _rectangle(box, dimensions, GREEN)
    return text
