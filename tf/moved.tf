moved {
  from = aws_cloudwatch_log_group.test_target
  to   = module.test_target.aws_cloudwatch_log_group.this
}

moved {
  from = aws_iam_role.test_lambda
  to   = module.test_target.aws_iam_role.this
}

moved {
  from = aws_iam_role_policy.test_lambda
  to   = module.test_target.aws_iam_role_policy.this
}

moved {
  from = aws_lambda_function.test_target
  to   = module.test_target.aws_lambda_function.this
}

moved {
  from = aws_cloudwatch_log_group.scheduler
  to   = module.scheduler.aws_cloudwatch_log_group.this
}

moved {
  from = aws_iam_role.scheduler_lambda
  to   = module.scheduler.aws_iam_role.this
}

moved {
  from = aws_iam_role_policy.scheduler_lambda
  to   = module.scheduler.aws_iam_role_policy.this
}

moved {
  from = aws_lambda_function.scheduler
  to   = module.scheduler.aws_lambda_function.this
}

moved {
  from = aws_lambda_function_event_invoke_config.scheduler
  to   = module.scheduler.aws_lambda_function_event_invoke_config.this[0]
}

moved {
  from = aws_scheduler_schedule_group.generated
  to   = module.target_invocation.aws_scheduler_schedule_group.this
}

moved {
  from = aws_iam_role.target_invocation
  to   = module.target_invocation.aws_iam_role.this
}

moved {
  from = aws_iam_role_policy.target_invocation
  to   = module.target_invocation.aws_iam_role_policy.this
}

moved {
  from = aws_scheduler_schedule_group.monthly
  to   = module.monthly_invocation.aws_scheduler_schedule_group.this
}

moved {
  from = aws_iam_role.monthly_invocation
  to   = module.monthly_invocation.aws_iam_role.this
}

moved {
  from = aws_iam_role_policy.monthly_invocation
  to   = module.monthly_invocation.aws_iam_role_policy.this
}
