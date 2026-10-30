from reframe.evaluation import score_identity


def test_raw_id_adoption_is_not_a_person_switch():
    primary = [0.2, 0, 0.3, 1]
    secondary = [0.7, 0, 0.8, 1]
    truth = {"frames": [{"t": 0, "primary": primary, "secondary": secondary}]}
    telemetry = [
        {"time_pos": t, "target_id": i, "target_box": primary, "gen": 1}
        for t, i in [(32, 1), (38, 4), (44.5, 4)]
    ]
    result = score_identity(telemetry, truth)
    assert result["raw_track_id_changes"] == 1
    assert result["pass"] is True
    telemetry[1]["target_box"] = secondary
    result = score_identity(telemetry, truth)
    assert result["target_person_switches"] == 2
    assert result["pass"] is False
