"""
Unit tests — football.py API response parser.
Tests _STAGE_MAP, winner mapping, duration → went_to_et / went_to_pens.
All HTTP calls are mocked — no real network traffic.
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Import helpers so we can call internals directly ─────────────────────────
import services.football as football


# ─────────────────────────────────────────────────────────────────────────────
# _STAGE_MAP correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestStageMap:
    def test_group_stage(self):
        assert football._STAGE_MAP["GROUP_STAGE"] == "group"

    def test_last_32_not_round_of_32(self):
        """API sends LAST_32, NOT ROUND_OF_32 — this was the original bug."""
        assert "ROUND_OF_32" not in football._STAGE_MAP
        assert football._STAGE_MAP["LAST_32"] == "round_of_32"

    def test_last_16(self):
        assert "ROUND_OF_16" not in football._STAGE_MAP
        assert football._STAGE_MAP["LAST_16"] == "round_of_16"

    def test_quarter_finals(self):
        assert football._STAGE_MAP["QUARTER_FINALS"] == "quarter_final"

    def test_semi_finals(self):
        assert football._STAGE_MAP["SEMI_FINALS"] == "semi_final"

    def test_final(self):
        assert football._STAGE_MAP["FINAL"] == "final"

    def test_third_place_treated_as_semi(self):
        assert football._STAGE_MAP["THIRD_PLACE"] == "semi_final"

    def test_unknown_stage_falls_back_to_group(self):
        assert football._STAGE_MAP.get("SOMETHING_WEIRD", "group") == "group"


# ─────────────────────────────────────────────────────────────────────────────
# _WINNER_MAP
# ─────────────────────────────────────────────────────────────────────────────

class TestWinnerMap:
    def test_home_team(self):
        assert football._WINNER_MAP["HOME_TEAM"] == "home"

    def test_away_team(self):
        assert football._WINNER_MAP["AWAY_TEAM"] == "away"

    def test_draw(self):
        assert football._WINNER_MAP["DRAW"] == "draw"


# ─────────────────────────────────────────────────────────────────────────────
# get_match_result — duration → went_to_et / went_to_pens
# ─────────────────────────────────────────────────────────────────────────────

def _mock_api_response(status, home, away, winner_raw, duration):
    return {
        "status": status,
        "score": {
            "fullTime": {"home": home, "away": away},
            "winner":   winner_raw,
            "duration": duration,
        },
    }


class TestGetMatchResult:
    def _call(self, payload):
        with patch.object(football, "_get", return_value=payload):
            with patch.object(football, "FOOTBALL_DATA_KEY", "fake-key"):
                return football.get_match_result(999)

    def test_regular_time_win(self):
        r = self._call(_mock_api_response("FINISHED", 2, 1, "HOME_TEAM", "REGULAR"))
        assert r["finished"] is True
        assert r["home_score"] == 2
        assert r["away_score"] == 1
        assert r["winner"] == "home"
        assert r["went_to_et"] is False
        assert r["went_to_pens"] is False

    def test_regular_time_draw(self):
        r = self._call(_mock_api_response("FINISHED", 1, 1, "DRAW", "REGULAR"))
        assert r["winner"] == "draw"
        assert r["went_to_et"] is False
        assert r["went_to_pens"] is False

    def test_extra_time_no_pens(self):
        r = self._call(_mock_api_response("FINISHED", 2, 1, "HOME_TEAM", "EXTRA_TIME"))
        assert r["went_to_et"] is True
        assert r["went_to_pens"] is False
        assert r["winner"] == "home"

    def test_penalty_shootout(self):
        """Score stays 1-1 (pens don't change it), but winner is set."""
        r = self._call(_mock_api_response("FINISHED", 1, 1, "AWAY_TEAM", "PENALTY_SHOOTOUT"))
        assert r["went_to_et"] is True       # pens implies ET
        assert r["went_to_pens"] is True
        assert r["winner"] == "away"
        assert r["home_score"] == 1
        assert r["away_score"] == 1

    def test_not_finished_yet(self):
        r = self._call(_mock_api_response("IN_PLAY", 0, 0, None, "REGULAR"))
        assert r["finished"] is False

    def test_awarded_counts_as_finished(self):
        r = self._call(_mock_api_response("AWARDED", 3, 0, "HOME_TEAM", "REGULAR"))
        assert r["finished"] is True

    def test_none_winner_raw_gives_none(self):
        r = self._call(_mock_api_response("FINISHED", 0, 0, None, "REGULAR"))
        assert r["winner"] is None

    def test_returns_none_when_no_api_key(self):
        with patch.object(football, "FOOTBALL_DATA_KEY", ""):
            result = football.get_match_result(999)
        assert result is None

    def test_returns_none_on_network_error(self):
        with patch.object(football, "_get", side_effect=Exception("timeout")):
            with patch.object(football, "FOOTBALL_DATA_KEY", "fake-key"):
                result = football.get_match_result(999)
        assert result is None
