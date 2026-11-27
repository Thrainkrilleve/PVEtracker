"""
Leaderboard roll-up tests — pure Python, no Django DB.

The helper is what the web leaderboard, the dashboard rank, and a later
Discord digest all call. These cases lock the grouping without booting Auth.
"""

# Standard Library
import unittest

from aa_pilotstats.leaderboard import normalize_sort_key, rank_of, rollup_sessions, user_main


class _Character:
    def __init__(self, character_id, character_name):
        self.character_id = character_id
        self.character_name = character_name


class _User:
    def __init__(self, pk, username, main=None):
        self.pk = pk
        self.username = username
        self.profile = type("Profile", (), {"main_character": main})()


class _Row:
    def __init__(self, user, character, **stats):
        self.uploaded_by = user
        self.uploaded_by_id = user.pk
        self.character = character
        for key, value in stats.items():
            setattr(self, key, value)


class UserMainTests(unittest.TestCase):
    def test_missing_profile_is_none(self):
        self.assertIsNone(user_main(type("User", (), {})()))


class RollupTests(unittest.TestCase):
    def test_alt_damage_rolls_up_to_the_main(self):
        main = _Character(1, "Main Character")
        alt = _Character(2, "Alt Character")
        user = _User(10, "pilot", main=main)
        rows = rollup_sessions([
            _Row(user, main, damage_out=1000, damage_in=10, reps_received_total=0, reps_given_total=0),
            _Row(user, alt, damage_out=500, damage_in=5, reps_received_total=0, reps_given_total=0),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], 1)
        self.assertEqual(rows[0]["label"], "Main Character")
        self.assertEqual(rows[0]["damage_out"], 1500)
        self.assertEqual(rows[0]["upload_count"], 2)
        self.assertEqual(
            [name for name, _stats in rows[0]["by_character"]],
            ["Main Character", "Alt Character"],
        )

    def test_users_without_a_main_stay_separate(self):
        first = _User(1, "pilot-one")
        second = _User(2, "pilot-two")
        char_a = _Character(11, "Character A")
        char_b = _Character(12, "Character B")
        rows = rollup_sessions([
            _Row(first, char_a, damage_out=100, damage_in=0, reps_received_total=0, reps_given_total=0),
            _Row(second, char_b, damage_out=50, damage_in=0, reps_received_total=0, reps_given_total=0),
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["label"], "pilot-one")
        self.assertEqual(rows[0]["key"], "user:1")
        self.assertEqual({row["key"] for row in rows}, {"user:1", "user:2"})

    def test_sorts_by_the_requested_field(self):
        main = _Character(1, "Main Character")
        user = _User(10, "pilot", main=main)
        other_char = _Character(3, "Other Character")
        other = _User(11, "other", main=other_char)
        rows = rollup_sessions(
            [
                _Row(user, main, damage_out=100, damage_in=5, reps_received_total=0, reps_given_total=9),
                _Row(other, other_char, damage_out=50, damage_in=1, reps_received_total=0, reps_given_total=40),
            ],
            sort_key="reps_given_total",
        )
        self.assertEqual([row["label"] for row in rows], ["Other Character", "Main Character"])

    def test_unknown_sort_falls_back_to_damage_dealt(self):
        self.assertEqual(normalize_sort_key("not-a-column"), "damage_out")
        main = _Character(1, "Main Character")
        user = _User(10, "pilot", main=main)
        other_char = _Character(3, "Other Character")
        other = _User(11, "other", main=other_char)
        rows = rollup_sessions(
            [
                _Row(user, main, damage_out=10, damage_in=0, reps_received_total=0, reps_given_total=0),
                _Row(other, other_char, damage_out=80, damage_in=0, reps_received_total=0, reps_given_total=0),
            ],
            sort_key="not-a-column",
        )
        self.assertEqual(rows[0]["label"], "Other Character")

    def test_extra_sum_field_is_grouped_the_same_way(self):
        """ISK (or kills) later on must use this grouping, not a second one."""
        main = _Character(1, "Main Character")
        alt = _Character(2, "Alt Character")
        user = _User(10, "pilot", main=main)
        fields = ("damage_out", "bounty_isk")
        rows = rollup_sessions(
            [
                _Row(user, main, damage_out=100, bounty_isk=1000),
                _Row(user, alt, damage_out=50, bounty_isk=250),
            ],
            sort_key="bounty_isk",
            sum_fields=fields,
        )
        self.assertEqual(rows[0]["bounty_isk"], 1250)
        self.assertEqual(rows[0]["damage_out"], 150)
        self.assertEqual(rows[0]["by_character"][0][1]["bounty_isk"], 1000)

    def test_rank_follows_the_sorted_buckets(self):
        main = _Character(1, "Main Character")
        user = _User(10, "pilot", main=main)
        other_char = _Character(3, "Other Character")
        other = _User(11, "other", main=other_char)
        absent = _User(12, "absent", main=_Character(4, "Absent Character"))
        rows = rollup_sessions([
            _Row(user, main, damage_out=10, damage_in=0, reps_received_total=0, reps_given_total=0),
            _Row(other, other_char, damage_out=80, damage_in=0, reps_received_total=0, reps_given_total=0),
        ])
        self.assertEqual(rank_of(rows, other), 1)
        self.assertEqual(rank_of(rows, user), 2)
        self.assertIsNone(rank_of(rows, absent))


if __name__ == "__main__":
    unittest.main()
