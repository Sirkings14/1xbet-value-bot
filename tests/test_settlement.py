import unittest

from settle_signals import settle_selection


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "completed": True,
            "home_team": "Home FC",
            "away_team": "Away FC",
            "scores": [
                {"name": "Home FC", "score": "2"},
                {"name": "Away FC", "score": "1"},
            ],
        }

    def test_h2h_home_win(self):
        self.assertEqual(
            settle_selection({"market": "h2h", "selection": "Home FC"}, self.event),
            "win",
        )

    def test_h2h_away_loss(self):
        self.assertEqual(
            settle_selection({"market": "h2h", "selection": "Away FC"}, self.event),
            "loss",
        )

    def test_totals(self):
        self.assertEqual(
            settle_selection({"market": "totals", "selection": "Over 2.5"}, self.event),
            "win",
        )
        self.assertEqual(
            settle_selection({"market": "totals", "selection": "Under 2.5"}, self.event),
            "loss",
        )

    def test_incomplete_event_is_not_settled(self):
        event = dict(self.event)
        event["completed"] = False
        self.assertIsNone(
            settle_selection({"market": "h2h", "selection": "Home FC"}, event)
        )


if __name__ == "__main__":
    unittest.main()
