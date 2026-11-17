import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from scheduler import create_schedule
from state import State


def aws_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "CreateSchedule")


def sample_plan():
    return {
        "job_id": "job-test",
        "schedule_name": "dyn-test",
        "local_timestamp": "2099-12-11T09:00:00",
        "execution_timestamp": "2099-12-11T09:00:00-05:00",
        "execution_date": "2099-12-11",
        "execution_time": "09:00:00",
        "timezone": "America/New_York",
        "target_arn": "arn:aws:lambda:us-east-1:123456789012:function:test-dynScheduler",
        "target_lambda_name": "test-dynScheduler",
        "schedule_description": "Friday after first Sunday",
    }


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.get_schedule.side_effect = aws_error("ResourceNotFoundException")
        self.client.create_schedule.return_value = {"ScheduleArn": "schedule-arn"}
        self.state = Mock()
        self.state.get.return_value = {}
        self.state.reserve.side_effect = [1, 2, 3]
        self.sleep = patch("scheduler.time.sleep").start()
        self.addCleanup(patch.stopall)

    def create(self):
        return create_schedule(self.client, self.state, sample_plan(), "role", "group")

    def test_creation_request(self):
        self.assertEqual(self.create()["status"], "created")
        request = self.client.create_schedule.call_args.kwargs
        self.assertEqual(request["ScheduleExpression"], "at(2099-12-11T09:00:00)")
        self.assertEqual(request["ScheduleExpressionTimezone"], "America/New_York")
        self.assertEqual(request["ActionAfterCompletion"], "DELETE")
        self.assertEqual(request["FlexibleTimeWindow"], {"Mode": "OFF"})
        self.assertEqual(request["Target"]["Input"], "{}")
        self.assertEqual(request["Name"], "dyn-test")
        self.assertEqual(request["ClientToken"], "job-test")
        self.client.delete_schedule.assert_not_called()

    def test_three_total_attempts(self):
        self.client.create_schedule.side_effect = aws_error("ThrottlingException")
        with self.assertRaises(ClientError):
            self.create()
        self.assertEqual(self.client.create_schedule.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_success_stops_early(self):
        self.client.create_schedule.side_effect = [
            aws_error("InternalServerException"), {"ScheduleArn": "schedule-arn"},
        ]
        self.create()
        self.assertEqual(self.client.create_schedule.call_count, 2)

    def test_permanent_error_is_not_retried(self):
        self.client.create_schedule.side_effect = aws_error("AccessDeniedException")
        with self.assertRaises(ClientError):
            self.create()
        self.assertEqual(self.client.create_schedule.call_count, 1)
        self.state.release.assert_called_once_with("schedule#job-test", terminal=True)

    def test_uncertain_success_is_reconciled(self):
        existing = {
            "Description": "dynamic-scheduler:job-test",
            "Arn": "schedule-arn", "ScheduleExpression": "at(2099-12-11T09:00:00)",
            "ScheduleExpressionTimezone": "America/New_York",
            "FlexibleTimeWindow": {"Mode": "OFF"}, "State": "ENABLED",
            "ActionAfterCompletion": "DELETE",
            "Target": {"Arn": sample_plan()["target_arn"], "RoleArn": "role", "Input": "{}"},
        }
        self.client.get_schedule.side_effect = [aws_error("ResourceNotFoundException"), existing]
        self.client.create_schedule.side_effect = aws_error("InternalServerException")
        self.assertEqual(self.create()["status"], "created")
        self.assertEqual(self.client.create_schedule.call_count, 1)

    def test_budget_and_previous_creation_cannot_be_bypassed(self):
        self.state.reserve.side_effect = RuntimeError("exhausted")
        with self.assertRaises(RuntimeError):
            self.create()
        self.client.create_schedule.assert_not_called()
        self.state.get.return_value = {"schedule_arn": "old-arn"}
        with self.assertRaisesRegex(RuntimeError, "refusing to recreate"):
            self.create()

    def test_existing_schedule_is_deleted_before_creation(self):
        self.client.get_schedule.side_effect = None
        self.client.get_schedule.return_value = {"ScheduleExpression": "wrong"}
        self.create()
        self.client.delete_schedule.assert_called_once_with(
            Name="dyn-test", GroupName="group", ClientToken="delete-job-test",
        )
        self.assertLess(
            [call[0] for call in self.client.method_calls].index("delete_schedule"),
            [call[0] for call in self.client.method_calls].index("create_schedule"),
        )
        self.state.acquire.assert_called_once()
        self.state.unlock.assert_called_once()

    def test_equivalent_schedule_from_other_job_is_also_replaced(self):
        self.client.get_schedule.side_effect = None
        self.client.get_schedule.return_value = {
            "ScheduleExpression": "at(2099-12-11T09:00:00)", "Description": "dynamic-scheduler:old-job",
        }
        self.create()
        self.client.delete_schedule.assert_called_once()
        self.client.create_schedule.assert_called_once()

    def test_own_schedule_mismatch_is_not_silently_accepted(self):
        self.client.get_schedule.side_effect = None
        self.client.get_schedule.return_value = {
            "Description": "dynamic-scheduler:job-test", "ScheduleExpression": "wrong",
        }
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.create()
        self.client.delete_schedule.assert_not_called()
        self.client.create_schedule.assert_not_called()

    def test_delete_failure_stops_creation(self):
        self.client.get_schedule.side_effect = None
        self.client.get_schedule.return_value = {"Arn": "old"}
        self.client.delete_schedule.side_effect = aws_error("AccessDeniedException")
        with self.assertRaises(ClientError):
            self.create()
        self.client.create_schedule.assert_not_called()
        self.state.unlock.assert_called_once()

    def test_delete_not_found_race_still_creates(self):
        self.client.get_schedule.side_effect = None
        self.client.get_schedule.return_value = {"Arn": "old"}
        self.client.delete_schedule.side_effect = aws_error("ResourceNotFoundException")
        self.create()
        self.client.create_schedule.assert_called_once()

    def test_new_month_has_separate_retry_budget_and_token(self):
        plan = {**sample_plan(), "job_id": "next-month"}
        create_schedule(self.client, self.state, plan, "role", "group")
        self.state.reserve.assert_called_once_with("schedule#next-month", 3)
        self.assertEqual(self.client.create_schedule.call_args.kwargs["ClientToken"], "next-month")

    def test_duplicate_delivery_does_not_delete_replacement(self):
        request = {
            "Arn": "schedule-arn", "Description": "dynamic-scheduler:job-test",
            "ScheduleExpression": "at(2099-12-11T09:00:00)",
            "ScheduleExpressionTimezone": "America/New_York",
            "FlexibleTimeWindow": {"Mode": "OFF"}, "State": "ENABLED",
            "ActionAfterCompletion": "DELETE",
            "Target": {"Arn": sample_plan()["target_arn"], "RoleArn": "role", "Input": "{}"},
        }
        self.client.get_schedule.side_effect = None
        self.client.get_schedule.return_value = request
        self.assertEqual(self.create()["status"], "already_exists")
        self.client.delete_schedule.assert_not_called()
        self.client.create_schedule.assert_not_called()

    def test_expired_plan_does_not_delete_existing_schedule(self):
        self.client.get_schedule.side_effect = None
        self.client.get_schedule.return_value = {"Arn": "old"}
        plan = {**sample_plan(), "execution_timestamp": "2000-01-01T00:00:00+00:00"}
        with self.assertRaisesRegex(ValueError, "expired"):
            create_schedule(self.client, self.state, plan, "role", "group")
        self.client.delete_schedule.assert_not_called()

    def test_lock_contention_prevents_mutation(self):
        self.state.acquire.side_effect = RuntimeError("in progress")
        with self.assertRaisesRegex(RuntimeError, "in progress"):
            self.create()
        self.client.delete_schedule.assert_not_called()
        self.client.create_schedule.assert_not_called()


class StateTests(unittest.TestCase):
    def test_durable_reservation_has_budget_and_lease_conditions(self):
        table = Mock()
        table.update_item.return_value = {"Attributes": {"creation_attempts": 2}}
        self.assertEqual(State(table).reserve("key", 3), 2)
        request = table.update_item.call_args.kwargs
        self.assertEqual(request["ExpressionAttributeValues"][":limit"], 3)
        self.assertIn("#attempts < :limit", request["ConditionExpression"])
        self.assertIn("#lease < :now", request["ConditionExpression"])

    def test_conditional_failure_surfaces(self):
        table = Mock()
        table.update_item.side_effect = aws_error("ConditionalCheckFailedException")
        with self.assertRaisesRegex(RuntimeError, "budget"):
            State(table).reserve("key", 3)
