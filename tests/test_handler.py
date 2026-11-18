import hashlib
import json
import os
import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

import handler


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

    def test_request_id_validation(self):
        for value in ("manual-001", "a" * 64):
            self.assertEqual(handler.validate_event({**EVENT, "request_id": value})["request_id"], value)
        for value in ("", " ", "a" * 65, "has spaces", "path/id", None, True, 123):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "request_id"):
                handler.validate_event({**EVENT, "request_id": value})

    @patch("handler.create_schedule", return_value={"schedule_name": "name", "status": "already_exists"})
    @patch("handler.State")
    @patch("handler.boto3")
    def test_legacy_job_identity_is_preserved(self, boto, state_type, create):
        inputs = {field: EVENT[field] for field in handler.FIELDS}
        expected = hashlib.sha256(json.dumps([inputs, "2026-11"], sort_keys=True).encode()).hexdigest()
        state_type.return_value.get.return_value = {"plan": {"saved": "plan"}}
        handler.lambda_handler(EVENT, Mock())
        state_type.return_value.get.assert_called_once_with(f"plan#{expected}")

    @patch("handler.parse_description", return_value=date(2099, 12, 11))
    @patch("handler.State")
    @patch("handler.boto3")
    def test_explicit_rerun_has_fresh_state_and_old_retry_cannot_replace_it(self, boto, state_type, parse):
        entries = {}
        current_schedule = {}
        state = state_type.return_value
        state.get.side_effect = lambda key: entries.get(key, {})
        state.save_plan.side_effect = lambda key, plan: entries.setdefault(key, {"plan": plan})["plan"]
        state.record.side_effect = lambda key, field, value: entries.setdefault(key, {}).update({field: value})
        state.reserve.return_value = 1
        scheduler = Mock()
        boto.client.return_value = scheduler

        def get_schedule(**request):
            if not current_schedule:
                raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetSchedule")
            return dict(current_schedule)

        def create_schedule(**request):
            current_schedule.update(request, Arn="schedule-arn")
            return {"ScheduleArn": "schedule-arn"}

        scheduler.get_schedule.side_effect = get_schedule
        scheduler.create_schedule.side_effect = create_schedule
        scheduler.delete_schedule.side_effect = lambda **request: current_schedule.clear()
        event = {key: value for key, value in EVENT.items() if key != "scheduled_at"}
        first_event = {**event, "request_id": "manual-001"}
        second_event = {**event, "request_id": "manual-002"}

        with patch("handler.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat("2026-11-17T19:00:00+00:00")
            first = handler.lambda_handler(first_event, Mock())
            clock.now.return_value = datetime.fromisoformat("2026-12-17T19:00:00+00:00")
            retry = handler.lambda_handler(first_event, Mock())
            self.assertEqual(retry["job_id"], first["job_id"])
            self.assertEqual(retry["status"], "already_exists")
            second = handler.lambda_handler(second_event, Mock())
            with self.assertRaisesRegex(RuntimeError, "refusing to recreate"):
                handler.lambda_handler(first_event, Mock())

        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertEqual(second["request_id"], "manual-002")
        self.assertEqual(second["status"], "created")
        self.assertEqual(parse.call_count, 2)
        self.assertEqual(state.save_plan.call_count, 2)
        self.assertEqual([call.args for call in state.reserve.call_args_list],
                         [(f"schedule#{first['job_id']}", 3), (f"schedule#{second['job_id']}", 3)])
        self.assertEqual([call.kwargs["ClientToken"] for call in scheduler.create_schedule.call_args_list],
                         [first["job_id"], second["job_id"]])
        scheduler.delete_schedule.assert_called_once()
        self.assertEqual(current_schedule["Description"], f"dynamic-scheduler:{second['job_id']}")

    @patch("handler.create_schedule")
    @patch("handler.parse_description")
    @patch("handler.State")
    @patch("handler.boto3")
    def test_end_to_end_coordination_and_disabled_email(self, boto, state_type, parse, create):
        state = state_type.return_value
        state.get.return_value = {}
        state.save_plan.side_effect = lambda key, plan: plan
        create.return_value = {"schedule_name": "name", "status": "created"}
        manual_event = {
            **{key: value for key, value in EVENT.items() if key != "scheduled_at"},
            "schedule_description": "2nd of the month after this quarter end",
        }
        cases = (
            (EVENT, "11/01/2026", date(2026, 11, 6), "2026-11-01T06:00:00+00:00"),
            ({**EVENT, "scheduled_at": "2026-11-01T02:00:00Z"}, "10/31/2026",
             date(2026, 11, 6), "2026-11-01T02:00:00+00:00"),
            (manual_event, "11/17/2026", date(2027, 1, 2), "2026-11-18T02:00:00+00:00"),
        )
        with patch("handler.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat("2026-11-18T02:00:00+00:00")
            for event, expected_date, model_date, reference in cases:
                with self.subTest(expected_date=expected_date):
                    parse.return_value = model_date
                    result = handler.lambda_handler(event, Mock())
                    parse.assert_called_with(
                        boto.client.return_value,
                        event["bedrock_model_id"],
                        f"{event['schedule_description']}, current date: {expected_date}",
                        datetime.fromisoformat(reference),
                        event["execution_time"],
                        handler.ZoneInfo(event["timezone"]),
                    )
                    self.assertEqual(result["notification_status"], "disabled")
                    boto.client.assert_any_call("bedrock-runtime", region_name="us-east-1", config=unittest.mock.ANY)
                    self.assertNotIn("sesv2", [call.args[0] for call in boto.client.call_args_list])
                    self.assertEqual(create.call_args.args[2]["execution_date"], model_date.isoformat())
                    self.assertEqual(create.call_args.args[2]["local_timestamp"],
                                     f"{model_date.isoformat()}T09:00:00")
                    self.assertEqual(create.call_args.args[2]["schedule_name"], EVENT["schedule_name"])
                    self.assertEqual(create.call_args.args[2]["schedule_description"], event["schedule_description"])
                    self.assertEqual(len(create.call_args.args[2]["job_id"]), 64)

    @patch("handler.create_schedule")
    @patch("handler.State")
    @patch("handler.boto3")
    def test_today_combines_model_date_with_input_time(self, boto, state_type, create):
        state = state_type.return_value
        state.get.return_value = {}
        state.save_plan.side_effect = lambda key, plan: plan
        boto.client.return_value.converse.return_value = {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": '{"execution_date":"2026-11-17"}'}]}},
        }
        create.return_value = {"schedule_name": "name", "status": "created"}
        event = {key: value for key, value in EVENT.items() if key != "scheduled_at"}
        event.update(schedule_description="today", execution_time="15:00:00")
        for reference, succeeds in (
            ("2026-11-17T19:28:00+00:00", True),
            ("2026-11-17T20:00:00+00:00", False),
            ("2026-11-17T20:01:00+00:00", False),
        ):
            with self.subTest(reference=reference), patch("handler.datetime", wraps=datetime) as clock:
                state.save_plan.reset_mock()
                create.reset_mock()
                clock.now.return_value = datetime.fromisoformat(reference)
                if succeeds:
                    handler.lambda_handler(event, Mock())
                    plan = create.call_args.args[2]
                    self.assertEqual(plan["execution_timestamp"], "2026-11-17T15:00:00-05:00")
                    self.assertEqual(plan["local_timestamp"], "2026-11-17T15:00:00")
                    self.assertEqual(plan["schedule_description"], "today")
                    request = boto.client.return_value.converse.call_args.kwargs
                    context = json.loads(request["messages"][0]["content"][0]["text"])
                    self.assertEqual(context["reference_timestamp"], "2026-11-17T14:28:00-05:00")
                    self.assertEqual(context["execution_time"], "15:00:00")
                    self.assertEqual(context["timezone"], "America/New_York")
                else:
                    with self.assertRaisesRegex(ValueError, "future"):
                        handler.lambda_handler(event, Mock())
                    state.save_plan.assert_not_called()
                    create.assert_not_called()

    @patch("handler.create_schedule")
    @patch("handler.State")
    @patch("handler.boto3")
    def test_invalid_model_dates_do_not_save_or_create(self, boto, state_type, create):
        state = state_type.return_value
        state.get.return_value = {}
        responses = (
            '{"execution_date":"2027-02-29"}',
            '{"execution_date":"2026-10-31"}',
            '{"execution_date":"2026-11-01","target_arn":"untrusted"}',
            '{"error":"ambiguous description"}',
        )
        for text in responses:
            boto.client.return_value.converse.return_value = {
                "stopReason": "end_turn", "output": {"message": {"content": [{"text": text}]}}
            }
            with self.subTest(text=text), self.assertRaises(ValueError):
                handler.lambda_handler(EVENT, Mock())
        state.save_plan.assert_not_called()
        create.assert_not_called()

    @patch("handler.State")
    @patch("handler.boto3")
    def test_saved_plan_skips_model_call(self, boto, state_type):
        state_type.return_value.get.return_value = {"plan": {"saved": "plan"}}
        with patch("handler.create_schedule", return_value={"schedule_name": "name", "status": "already_exists"}):
            handler.lambda_handler(EVENT, Mock())
        self.assertNotIn("bedrock-runtime", [call.args[0] for call in boto.client.call_args_list])
