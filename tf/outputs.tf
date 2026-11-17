output "scheduler_function_name" {
  value = module.scheduler.function.function_name
}

output "scheduler_function_arn" {
  value = module.scheduler.function.arn
}

output "test_target_function_name" {
  value = module.test_target.function.function_name
}

output "test_target_function_arn" {
  value = module.test_target.function.arn
}

output "state_table_name" {
  value = aws_dynamodb_table.state.name
}

output "schedule_group_name" {
  value = module.target_invocation.group.name
}

output "target_invocation_role_arn" {
  value = module.target_invocation.role_arn
}

output "monthly_trigger_name" {
  value = aws_scheduler_schedule.monthly.name
}

output "manual_test_payload" {
  description = "Manual invocation payload without the Scheduler-only scheduled_at placeholder."
  value       = jsonencode(local.schedule_input)
}
