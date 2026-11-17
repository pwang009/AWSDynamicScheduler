import json
import unittest
from unittest.mock import Mock

from bedrock_parser import parse_description
from schedule_description import Rule


class ParserTests(unittest.TestCase):
    def test_valid_rule_and_request(self):
        client = Mock()
        client.converse.return_value = {
            "stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps({
                "target_weekday": "Friday", "relationship": "strictly_after",
                "anchor_ordinal": 1, "anchor_weekday": "Sunday",
            })}]}}
        }
        self.assertEqual(parse_description(client, "amazon.nova-pro-v1:0", "Friday after first Sunday"), Rule(4, 1, 6))
        request = client.converse.call_args.kwargs
        self.assertEqual(request["modelId"], "amazon.nova-pro-v1:0")
        self.assertEqual(json.loads(request["messages"][0]["content"][0]["text"]),
                         {"description": "Friday after first Sunday"})
        self.assertNotIn("toolConfig", request)

    def test_invalid_or_incomplete_responses(self):
        for reason, content in (
            ("max_tokens", [{"text": "{}"}]),
            ("guardrail_intervened", [{"text": "{}"}]),
            ("end_turn", [{"text": "not json"}]),
            ("end_turn", [{"text": '{"error":"unsupported"}'}]),
            ("end_turn", [{"text": '{"a":1,"a":2}'}]),
            ("end_turn", [{"toolUse": {}}]),
            ("end_turn", []),
        ):
            client = Mock()
            client.converse.return_value = {"stopReason": reason, "output": {"message": {"content": content}}}
            with self.subTest(reason=reason, content=content), self.assertRaises(ValueError):
                parse_description(client, "model", "description")
