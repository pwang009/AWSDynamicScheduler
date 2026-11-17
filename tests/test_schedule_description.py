import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from schedule_description import Rule, local_timestamp, resolve_date, validate_rule


class DateTests(unittest.TestCase):
    def test_examples_and_boundary(self):
        zone = ZoneInfo("America/New_York")
        for reference in ("2026-11-17T09:00:00-05:00", "2026-12-01T01:00:00-05:00",
                          "2026-11-06T09:00:00-05:00"):
            with self.subTest(reference=reference):
                result = resolve_date(Rule(4, 1, 6), "09:00:00", zone, datetime.fromisoformat(reference))
                self.assertEqual(result.isoformat(), "2026-12-11T09:00:00-05:00")

    def test_cross_year_same_weekday_and_leap_year(self):
        zone = ZoneInfo("UTC")
        cases = [
            (Rule(0, 4, 3), "2026-12-01", "2026-12-28"),
            (Rule(0, 5, 3), "2026-12-01", "2027-01-04"),
            (Rule(6, 1, 6), "2026-12-01", "2026-12-13"),
            (Rule(4, 4, 3), "2024-02-01", "2024-02-23"),
        ]
        for rule, start, expected in cases:
            with self.subTest(rule=rule):
                result = resolve_date(rule, "09:00:00", zone, datetime.fromisoformat(start).replace(tzinfo=zone))
                self.assertEqual(str(result.date()), expected)

    def test_invalid_fifth_weekday(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            resolve_date(Rule(4, 5, 6), "09:00:00", ZoneInfo("UTC"), datetime(2026, 2, 1, tzinfo=timezone.utc))

    def test_dst_gaps_and_folds_are_rejected(self):
        for clock in (datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                local_timestamp(clock, ZoneInfo("America/New_York"))

    def test_eastern_offsets(self):
        for month, offset in ((1, -5 * 3600), (7, -4 * 3600)):
            result = local_timestamp(datetime(2026, month, 1, 9), ZoneInfo("America/New_York"))
            self.assertEqual(result.utcoffset().total_seconds(), offset)

    def test_rule_validation(self):
        good = {"target_weekday": "Friday", "relationship": "strictly_after",
                "anchor_ordinal": 1, "anchor_weekday": "Sunday"}
        self.assertEqual(validate_rule(good), Rule(4, 1, 6))
        invalid = [
            {**good, "anchor_ordinal": True}, {**good, "anchor_ordinal": 6},
            {**good, "target_weekday": "Fri"}, {**good, "relationship": "before"},
            {**good, "target_arn": "untrusted"}, {"error": "ambiguous"}, [],
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_rule(value)
