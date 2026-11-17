import os
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from notifications import send_creation_email
from test_scheduler import sample_plan


EMAIL_ENV = {
    "EMAIL_PROVIDER": "ses",
    "EMAIL_SENDER": "sender@example.com",
    "EMAIL_RECIPIENTS": '["recipient@example.com"]',
    "EMAIL_MAX_ATTEMPTS": "3",
    "EMAIL_RETRY_BASE_SECONDS": "0",
    "EMAIL_RECOVERY_POLICY": "manual",
}


class NotificationTests(unittest.TestCase):
    def setUp(self):
        patch.dict(os.environ, EMAIL_ENV).start()
        patch("notifications.time.sleep").start()
        self.addCleanup(patch.stopall)
        self.state = Mock()
        self.state.get.return_value = {}
        self.state.reserve.side_effect = [1, 2, 3]
        self.client = Mock()
        self.client.send_email.return_value = {"MessageId": "message"}
        self.schedule = {**sample_plan(), "state_key": "schedule#dyn-test", "schedule_arn": "arn"}

    def test_email_content_and_delivery_tracking(self):
        self.assertEqual(send_creation_email(self.schedule, self.state, self.client), "message")
        request = self.client.send_email.call_args.kwargs
        self.assertEqual(request["Destination"]["ToAddresses"], ["recipient@example.com"])
        for field in ("execution_date", "execution_time", "timezone", "schedule_arn"):
            self.assertIn(self.schedule[field], request["Content"]["Simple"]["Body"]["Text"]["Data"])
        self.state.record.assert_any_call("schedule#dyn-test", "email_message_id", "message")

    def test_no_resend_after_recorded_delivery(self):
        self.state.get.return_value = {"email_message_id": "previous"}
        self.assertEqual(send_creation_email(self.schedule, self.state, self.client), "previous")
        self.client.send_email.assert_not_called()

    def test_email_failure_is_separate_from_creation(self):
        self.client.send_email.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "retry"}}, "SendEmail",
        )
        with self.assertRaisesRegex(RuntimeError, "Schedule exists"):
            send_creation_email(self.schedule, self.state, self.client)
        self.assertEqual(self.client.send_email.call_count, 3)

    def test_unsupported_policy_is_explicit(self):
        with patch.dict(os.environ, {"EMAIL_RECOVERY_POLICY": "automatic"}):
            with self.assertRaisesRegex(ValueError, "manual"):
                send_creation_email(self.schedule, self.state, self.client)
