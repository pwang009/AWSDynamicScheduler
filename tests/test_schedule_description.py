import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from schedule_description import local_timestamp, resolve_date, validate_date


class DateTests(unittest.TestCase):
    def test_resolved_dates_preserve_time_and_timezone(self):
        zone = ZoneInfo("America/New_York")
        for day, expected in (
            ("2026-12-11", "2026-12-11T09:00:00-05:00"),
            ("2027-01-02", "2027-01-02T09:00:00-05:00"),
            ("2028-02-29", "2028-02-29T09:00:00-05:00"),
            ("2027-07-02", "2027-07-02T09:00:00-04:00"),
        ):
            with self.subTest(day=day):
                result = resolve_date(validate_date({"execution_date": day}), "09:00:00", zone,
                                      datetime.fromisoformat("2026-11-17T09:00:00-05:00"))
                self.assertEqual(result.isoformat(), expected)

    def test_nonfuture_dates_are_rejected_not_rolled_forward(self):
        zone = ZoneInfo("America/New_York")
        reference = datetime.fromisoformat("2026-11-18T02:00:00+00:00")
        for day, clock in ((date(2026, 11, 16), "23:59:59"),
                           (date(2026, 11, 17), "20:59:59"),
                           (date(2026, 11, 17), "21:00:00")):
            with self.subTest(day=day, clock=clock), self.assertRaisesRegex(ValueError, "future"):
                resolve_date(day, clock, zone, reference)
        self.assertEqual(resolve_date(date(2026, 11, 17), "21:00:01", zone, reference).isoformat(),
                         "2026-11-17T21:00:01-05:00")

    def test_naive_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            resolve_date(date(2027, 1, 2), "09:00:00", ZoneInfo("UTC"), datetime(2026, 11, 17))

    def test_dst_gaps_and_folds_are_rejected(self):
        for clock in (datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                resolve_date(clock.date(), clock.strftime("%H:%M:%S"), ZoneInfo("America/New_York"),
                             datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_eastern_offsets(self):
        for month, offset in ((1, -5 * 3600), (7, -4 * 3600)):
            result = local_timestamp(datetime(2026, month, 1, 9), ZoneInfo("America/New_York"))
            self.assertEqual(result.utcoffset().total_seconds(), offset)

    def test_model_date_validation(self):
        good = {"execution_date": "2027-01-02"}
        self.assertEqual(validate_date(good), date(2027, 1, 2))
        invalid = [
            {"execution_date": value} for value in (
                True, None, 20270102, "2027-1-2", "20270102", "01/02/2027",
                "2027-02-29", "2026-04-31", "2027-01-02T09:00:00", "2027-01-02 ",
                "0000-01-01", "10000-01-01",
            )
        ]
        invalid += [{**good, "target_arn": "untrusted"}, {**good, "timezone": "UTC"},
                    {"error": "ambiguous"}, {}, [], {"target_weekday": "Friday"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_date(value)
