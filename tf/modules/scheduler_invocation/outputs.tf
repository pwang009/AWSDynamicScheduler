output "group" {
  value = aws_scheduler_schedule_group.this
}

output "role_arn" {
  description = "Invocation role ARN, available after its invocation permission is configured."
  value       = aws_iam_role.this.arn
  depends_on  = [aws_iam_role_policy.this]
}
