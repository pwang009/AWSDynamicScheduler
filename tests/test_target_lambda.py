import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from test_lambda.app import lambda_handler


class TestTargetLambda(unittest.TestCase):
    def test_scheduled_invocation_returns_confirmation_and_logs_request(self):
        context = SimpleNamespace(aws_request_id="test-request")
        for event in ({}, {"source": "local-test"}):
            with self.subTest(event=event), self.assertLogs("test_lambda.app", level="INFO") as logs:
                result = lambda_handler(event, context)
                self.assertEqual(result["received_event"], event)
                self.assertEqual(result["request_id"], "test-request")
                self.assertIn("invoked successfully", result["message"])
                self.assertEqual(datetime.fromisoformat(result["executed_at"]).tzinfo, timezone.utc)
                self.assertIn("request_id=test-request", logs.output[0])
