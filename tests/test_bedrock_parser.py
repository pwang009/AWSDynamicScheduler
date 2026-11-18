import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from bedrock_parser import parse_description

REFERENCE = datetime(2026, 11, 17, 19, 0, tzinfo=timezone.utc)


def parse(client, description):
    return parse_description(client, "amazon.nova-pro-v1:0", description,
                             REFERENCE, "09:00:00", ZoneInfo("America/New_York"))


class ParserTests(unittest.TestCase):
    def test_ordinal_weekday_rule_resolves_next_month_without_bedrock(self):
        client = Mock()
        result = parse(
            client,
            "the Friday after the 1st Sunday, current date: 11/17/2026",
        )
        self.assertEqual(result, date(2026, 12, 11))
        client.converse.assert_not_called()

    def test_valid_date_and_request(self):
        client = Mock()
        client.converse.return_value = {
            "stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps({
                "execution_date": "2027-01-02",
            })}]}}
        }
        description = "2nd of the month after this quarter end, current date: 11/17/2026"
        self.assertEqual(parse(client, description), date(2027, 1, 2))
        request = client.converse.call_args.kwargs
        self.assertEqual(request["modelId"], "amazon.nova-pro-v1:0")
        self.assertEqual(json.loads(request["messages"][0]["content"][0]["text"]),
                         {"description": description,
                          "reference_timestamp": "2026-11-17T14:00:00-05:00",
                          "execution_time": "09:00:00", "timezone": "America/New_York"})
        self.assertNotIn("toolConfig", request)

    def test_invalid_or_incomplete_responses(self):
        for reason, content in (
            ("max_tokens", [{"text": "{}"}]),
            ("guardrail_intervened", [{"text": "{}"}]),
            ("end_turn", [{"text": "not json"}]),
            ("end_turn", [{"text": '{"error":"unsupported"}'}]),
            ("end_turn", [{"text": '{"a":1,"a":2}'}]),
            ("end_turn", [{"text": '{"execution_date":"2027-01-02","execution_date":"2027-04-02"}'}]),
            ("end_turn", [{"text": '{"execution_date":"2027-02-29"}'}]),
            ("end_turn", [{"text": '{"execution_date":"2027-01-02","execution_time":"12:00:00"}'}]),
            ("end_turn", [{"toolUse": {}}]),
            ("end_turn", []),
        ):
            client = Mock()
            client.converse.return_value = {"stopReason": reason, "output": {"message": {"content": content}}}
            with self.subTest(reason=reason, content=content), self.assertRaises(ValueError):
                parse(client, "description")
