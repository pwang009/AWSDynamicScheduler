output "function" {
  description = "Deployed Lambda configuration, after optional asynchronous retry configuration."
  value       = aws_lambda_function.this
  depends_on  = [aws_lambda_function_event_invoke_config.this]
}

output "maximum_retry_attempts" {
  value = one(aws_lambda_function_event_invoke_config.this[*].maximum_retry_attempts)
}
