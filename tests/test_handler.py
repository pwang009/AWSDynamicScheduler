import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import handler
from schedule_description import Rule


EVENT = {
    "schedule_name": "example-monthly-schedule",
    "schedule_description": "Friday after first Sunday",
    "target_lambda_name": "test-dynScheduler",
    "execution_time": "09:00:00",
    "timezone": "America/New_York",
    "bedrock_model_id": "amazon.nova-pro-v1:0",
    "bedrock_region": "us-east-1",
    "scheduled_at": "2026-11-01T06:00:00Z",
}
ENV = {
    "TARGET_LAMBDA_ARN": "arn:aws:lambda:us-east-1:123456789012:function:test-dynScheduler",
    "ALLOWED_BEDROCK_MODEL_ID": "amazon.nova-pro-v1:0",
    "ALLOWED_BEDROCK_REGION": "us-east-1",
    "STATE_TABLE": "table",
    "SCHEDULE_GROUP": "group",
    "SCHEDULER_ROLE_ARN": "role",
}


class HandlerTests(unittest.TestCase):
    def setUp(self):
        patch.dict(os.environ, ENV).start()
        self.addCleanup(patch.stopall)

    def test_validation(self):
        handler.validate_event(EVENT)
        for field, value in (
            ("execution_time", "24:00:00"), ("execution_time", "9:00:00"),
            ("bedrock_region", "us-west-2"), ("target_lambda_name", "other-dynScheduler"),
            ("target_lambda_name", "wrong"), ("schedule_description", ""),
            ("schedule_name", "a" * 65), ("schedule_name", "invalid/name"),
            ("schedule_name", "has spaces"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                handler.validate_event({**EVENT, field: value})
        with self.assertRaises(ValueError):
            handler.validate_event([EVENT])
        for field in handler.FIELDS:
            missing = dict(EVENT)
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                handler.validate_event(missing)
        for clock in ("00:00:00", "23:59:59"):
            handler.validate_event({**EVENT, "execution_time": clock})
        for name in ("a", "a" * 64, "Schedule_2026.11-test"):
            handler.validate_event({**EVENT, "schedule_name": name})

    @patch("handler.create_schedule")
    @patch("handler.resolve_date")
    @patch("handler.parse_description")
    @patch("handler.State")
    @patch("handler.boto3")
    def test_end_to_end_coordination_and_disabled_email(self, boto, state_type, parse, resolve, create):
        state = state_type.return_value
        state.get.return_value = {}
        state.save_plan.side_effect = lambda key, plan: plan
        parse.return_value = Rule(4, 1, 6)
        resolve.return_value = datetime.fromisoformat("2026-11-06T09:00:00-05:00")
        create.return_value = {"schedule_name": "name", "status": "created"}
        result = handler.lambda_handler(EVENT, Mock())
        self.assertEqual(result["notification_status"], "disabled")
        boto.client.assert_any_call("bedrock-runtime", region_name="us-east-1", config=unittest.mock.ANY)
        self.assertNotIn("sesv2", [call.args[0] for call in boto.client.call_args_list])
        self.assertEqual(create.call_args.args[2]["execution_date"], "2026-11-06")
        self.assertEqual(create.call_args.args[2]["schedule_name"], EVENT["schedule_name"])
        self.assertEqual(len(create.call_args.args[2]["job_id"]), 64)

    @patch("handler.State")
    @patch("handler.boto3")
    def test_saved_plan_skips_model_call(self, boto, state_type):
        state_type.return_value.get.return_value = {"plan": {"saved": "plan"}}
        with patch("handler.create_schedule", return_value={"schedule_name": "name", "status": "already_exists"}):
            handler.lambda_handler(EVENT, Mock())
        self.assertNotIn("bedrock-runtime", [call.args[0] for call in boto.client.call_args_list])
