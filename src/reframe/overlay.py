"""Map tracked source rectangles into an ASS overlay in window coordinates."""


def make_overlay(record, dimensions):
    slash = chr(92)
    hud = (
        f"Reframe: {record['state']} | ID {record['target_id']} | "
        f"{record['sample_rate']:.1f} Hz | lead {record['lookahead']:.2f}s"
        + slash
        + "N"
        f"decode {record['decoder_rtf']:.1f}x | behind {record['behind']}"
    )
    text = (
        "{"
        + slash
        + "an7"
        + slash
        + "pos(12,12)"
        + slash
        + "fs18"
        + slash
        + "bord1}"
        + hud
    )
    box = record.get("target_box")
    if box:
        w, h = dimensions["w"], dimensions["h"]
        sx = w - dimensions["ml"] - dimensions["mr"]
        sy = h - dimensions["mt"] - dimensions["mb"]
        x0, x1 = (round(dimensions["ml"] + box[i] * sx) for i in [0, 2])
        y0, y1 = (round(dimensions["mt"] + box[i] * sy) for i in [1, 3])
        tags = (
            "{"
            + slash
            + "an7"
            + slash
            + "pos(0,0)"
            + slash
            + "p1"
            + slash
            + "bord2"
            + slash
            + "shad0"
            + slash
            + "1a&HFF&"
            + slash
            + "3c&H00FF00&}"
        )
        text += "\n" + tags + f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1} {x0} {y0}"
    return text
