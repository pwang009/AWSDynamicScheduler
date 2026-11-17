output "scheduler_function_name" {
  value = aws_lambda_function.scheduler.function_name
}

output "scheduler_function_arn" {
  value = aws_lambda_function.scheduler.arn
}

output "test_target_function_name" {
  value = aws_lambda_function.test_target.function_name
}

output "test_target_function_arn" {
  value = aws_lambda_function.test_target.arn
}

output "state_table_name" {
  value = aws_dynamodb_table.state.name
}

output "schedule_group_name" {
  value = aws_scheduler_schedule_group.generated.name
}

output "target_invocation_role_arn" {
  value = aws_iam_role.target_invocation.arn
}

output "monthly_trigger_name" {
  value = aws_scheduler_schedule.monthly.name
}

output "manual_test_payload" {
  description = "Manual invocation payload without the Scheduler-only scheduled_at placeholder."
  value       = jsonencode(local.schedule_input)
}
