"""
Parsing tests — pure Python, no Django DB needed.

Line shapes below are copied verbatim (markup included) from real EVE
Gamelog files used to validate the standalone prototype this was ported
from — see the module docstring in parsing.py.
"""

# Standard Library
import unittest

from aa_pvetracker.parsing import ParseStats, iter_events_from_lines, read_header_from_lines, summarize

HEADER = [
    "------------------------------------------------------------\n",
    "  Gamelog\n",
    "  Listener: Thrain Krill\n",
    "  Session Started: 2026.09.14 22:27:51\n",
    "------------------------------------------------------------\n",
]

DAMAGE_OUT = (
    "[ 2026.09.14 23:14:14 ] (combat) <color=0xff00ffff><b>1022</b> "
    "<color=0x77ffffff><font size=10>to</font> <b><color=0xffffffff>Bewildering Lathe</b>"
    "<font size=10><color=0x77ffffff> - Mega Pulse Laser II - Hits\n"
)
DAMAGE_IN = (
    "[ 2026.09.14 23:16:53 ] (combat) <color=0xffcc0000><b>762</b> "
    "<color=0x77ffffff><font size=10>from</font> <b><color=0xffffffff>Alienator Crusher</b>"
    "<font size=10><color=0x77ffffff> - Glances Off\n"
)
MISS_OUT = (
    "[ 2026.09.14 23:17:05 ] (combat) Your group of Mega Pulse Laser II misses "
    "Predictive Nanoswarm completely - Mega Pulse Laser II\n"
)
MISS_IN = "[ 2026.09.09 00:00:00 ] (combat) Blackout Handler misses you completely\n"
JAMMED = (
    "[ 2026.09.09 00:16:14 ] (combat) <color=0x77ffffff><font size=10>You're</font> "
    "<color=0xffffffff><b>jammed</b> <color=0x77ffffff><font size=10>by</font> "
    "<color=0xffffffff><b>Blackout Handler</b><color=0x77ffffff><font size=10> - Blackout Handler</font>\n"
)
REMOTE_REP_IN = (
    "[ 2026.09.09 00:21:43 ] (combat) <color=0xffccff66><b>448</b>"
    "<color=0x77ffffff><font size=10> remote armor repaired by </font>"
    '<b><color=0xffffffff>Tyler Lynch [C5-F]<FRT> - Guardian "Guardian"</b>'
    "<color=0x77ffffff><font size=10> - Large Coaxial Compact Remote Armor Repairer</font>\n"
)
# Unverified shape (see parsing.py docstring) — mirrors REMOTE_REP_IN with "to" instead of "by".
REMOTE_REP_OUT = (
    "[ 2026.09.09 00:21:43 ] (combat) <color=0xffccff66><b>500</b>"
    "<color=0x77ffffff><font size=10> remote armor repaired to </font>"
    '<b><color=0xffffffff>Some Fleetmate [ABC-D]<XYZ> - Nestor "Logi"</b>'
    "<color=0x77ffffff><font size=10> - Large Coaxial Compact Remote Armor Repairer</font>\n"
)
NON_COMBAT = "[ 2026.09.14 22:52:03 ] (notify) Starting clone jumping\n"


class ReadHeaderTests(unittest.TestCase):
    def test_reads_listener_and_session_started(self):
        info = read_header_from_lines(HEADER)
        self.assertEqual(info["character"], "Thrain Krill")
        self.assertEqual(info["session_started"], "2026.09.14 22:27:51")


class ParseLineTests(unittest.TestCase):
    def test_damage_out(self):
        stats = ParseStats()
        events = list(iter_events_from_lines([DAMAGE_OUT], stats))
        self.assertEqual(stats.combat_lines, 1)
        self.assertEqual(stats.matched, 1)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e.kind, "damage_out")
        self.assertEqual(e.amount, 1022)
        self.assertEqual(e.other_party, "Bewildering Lathe")
        self.assertEqual(e.weapon, "Mega Pulse Laser II")
        self.assertEqual(e.quality, "Hits")

    def test_damage_in(self):
        events = list(iter_events_from_lines([DAMAGE_IN]))
        e = events[0]
        self.assertEqual(e.kind, "damage_in")
        self.assertEqual(e.amount, 762)
        self.assertEqual(e.other_party, "Alienator Crusher")
        self.assertEqual(e.quality, "Glances Off")

    def test_miss_out(self):
        e = list(iter_events_from_lines([MISS_OUT]))[0]
        self.assertEqual(e.kind, "miss_out")
        self.assertEqual(e.other_party, "Predictive Nanoswarm")

    def test_miss_in(self):
        e = list(iter_events_from_lines([MISS_IN]))[0]
        self.assertEqual(e.kind, "miss_in")
        self.assertEqual(e.other_party, "Blackout Handler")

    def test_jammed(self):
        e = list(iter_events_from_lines([JAMMED]))[0]
        self.assertEqual(e.kind, "jammed")
        self.assertEqual(e.other_party, "Blackout Handler")

    def test_remote_rep_in(self):
        e = list(iter_events_from_lines([REMOTE_REP_IN]))[0]
        self.assertEqual(e.kind, "remote_rep_in")
        self.assertEqual(e.amount, 448)
        self.assertIn("Tyler Lynch", e.other_party)

    def test_remote_rep_out(self):
        e = list(iter_events_from_lines([REMOTE_REP_OUT]))[0]
        self.assertEqual(e.kind, "remote_rep_out")
        self.assertEqual(e.amount, 500)
        self.assertIn("Some Fleetmate", e.other_party)

    def test_non_combat_line_is_ignored(self):
        stats = ParseStats()
        events = list(iter_events_from_lines([NON_COMBAT], stats))
        self.assertEqual(stats.combat_lines, 0)
        self.assertEqual(events, [])


class SummarizeTests(unittest.TestCase):
    def test_full_session_summary(self):
        lines = [DAMAGE_OUT, DAMAGE_IN, MISS_OUT, MISS_IN, REMOTE_REP_IN, REMOTE_REP_OUT]
        events = list(iter_events_from_lines(lines))
        summary = summarize(events)

        self.assertEqual(summary["damage_out"], 1022)
        self.assertEqual(summary["damage_in"], 762)
        self.assertEqual(summary["shots_out"], 2)  # 1 hit + 1 miss
        self.assertEqual(summary["shots_in"], 2)   # 1 hit + 1 miss
        self.assertEqual(summary["reps_received_total"], 448)
        self.assertEqual(summary["reps_given_total"], 500)
        self.assertEqual(summary["top_targets"], [["Bewildering Lathe", 1022]])
        self.assertEqual(summary["top_attackers"], [["Alienator Crusher", 762]])

        # feed is most-recent-first and skips nothing but "other"/energy-neutral events
        self.assertEqual(len(summary["feed"]), 6)
        self.assertEqual(summary["feed"][0]["kind"], "remote_rep_out")
        self.assertEqual(summary["feed"][-1]["kind"], "damage_out")

    def test_feed_is_capped_and_most_recent_first(self):
        lines = [DAMAGE_OUT] * 70  # far more than FEED_LIMIT
        events = list(iter_events_from_lines(lines))
        summary = summarize(events)
        self.assertEqual(len(summary["feed"]), 60)


if __name__ == "__main__":
    unittest.main()
