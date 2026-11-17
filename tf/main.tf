data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  scheduler_name = "${var.project_name}-scheduler"
  target_name    = "test-schedule-lambda-dynScheduler"
  schedule_input = {
    schedule_name        = var.schedule_name
    schedule_description = var.schedule_description
    target_lambda_name   = local.target_name
    execution_time       = var.execution_time
    timezone             = var.timezone
    bedrock_model_id     = var.bedrock_model_id
    bedrock_region       = var.bedrock_region
  }
  lambda_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

data "archive_file" "scheduler" {
  type        = "zip"
  source_dir  = "${path.module}/.build/scheduler"
  output_path = "${path.module}/.build/scheduler.zip"
}

data "archive_file" "test_target" {
  type        = "zip"
  source_file = "${path.module}/../test_lambda/app.py"
  output_path = "${path.module}/.build/test-target.zip"
}

resource "aws_dynamodb_table" "state" {
  name         = "${var.project_name}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_scheduler_schedule_group" "generated" {
  name = "${var.project_name}-generated"
}

resource "aws_scheduler_schedule_group" "monthly" {
  name = "${var.project_name}-monthly"
}

resource "aws_cloudwatch_log_group" "scheduler" {
  name              = "/aws/lambda/${local.scheduler_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "test_target" {
  name              = "/aws/lambda/${local.target_name}"
  retention_in_days = 14
}

resource "aws_iam_role" "scheduler_lambda" {
  name               = "${var.project_name}-lambda"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role" "test_lambda" {
  name               = "${var.project_name}-test-lambda"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy" "test_lambda" {
  name = "WriteTestLogs"
  role = aws_iam_role.test_lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.test_target.arn}:*"
    }]
  })
}

resource "aws_lambda_function" "test_target" {
  function_name    = local.target_name
  role             = aws_iam_role.test_lambda.arn
  runtime          = "python3.12"
  handler          = "app.lambda_handler"
  filename         = data.archive_file.test_target.output_path
  source_code_hash = data.archive_file.test_target.output_base64sha256
  timeout          = 10
  memory_size      = 128

  depends_on = [aws_iam_role_policy.test_lambda]
}

resource "aws_iam_role" "target_invocation" {
  name = "${var.project_name}-invoke-target"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnEquals    = { "aws:SourceArn" = aws_scheduler_schedule_group.generated.arn }
      }
    }]
  })
}

resource "aws_iam_role_policy" "target_invocation" {
  name = "InvokeTestTarget"
  role = aws_iam_role.target_invocation.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.test_target.arn
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_lambda" {
  name = "ManageDynamicSchedules"
  role = aws_iam_role.scheduler_lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.scheduler.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Effect   = "Allow"
        Action   = ["scheduler:CreateSchedule", "scheduler:GetSchedule", "scheduler:DeleteSchedule"]
        Resource = "arn:${data.aws_partition.current.partition}:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/${aws_scheduler_schedule_group.generated.name}/*"
      },
      {
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = aws_iam_role.target_invocation.arn
        Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } }
      },
      {
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = "arn:${data.aws_partition.current.partition}:bedrock:${var.bedrock_region}::foundation-model/${var.bedrock_model_id}"
      }
    ]
  })
}

resource "aws_lambda_function" "scheduler" {
  function_name    = local.scheduler_name
  role             = aws_iam_role.scheduler_lambda.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.scheduler.output_path
  source_code_hash = data.archive_file.scheduler.output_base64sha256
  timeout          = 120
  memory_size      = 256

  environment {
    variables = {
      STATE_TABLE              = aws_dynamodb_table.state.name
      SCHEDULE_GROUP           = aws_scheduler_schedule_group.generated.name
      TARGET_LAMBDA_ARN        = aws_lambda_function.test_target.arn
      SCHEDULER_ROLE_ARN       = aws_iam_role.target_invocation.arn
      ALLOWED_BEDROCK_MODEL_ID = var.bedrock_model_id
      ALLOWED_BEDROCK_REGION   = var.bedrock_region
      EMAIL_PROVIDER           = var.email.provider
      EMAIL_SENDER             = var.email.sender
      EMAIL_RECIPIENTS         = jsonencode(var.email.recipients)
      EMAIL_MAX_ATTEMPTS       = tostring(var.email.max_attempts)
      EMAIL_RETRY_BASE_SECONDS = tostring(var.email.retry_base_seconds)
      EMAIL_RECOVERY_POLICY    = var.email.recovery_policy
    }
  }

  depends_on = [
    aws_iam_role_policy.scheduler_lambda,
    aws_iam_role_policy.target_invocation,
  ]
}

resource "aws_lambda_function_event_invoke_config" "scheduler" {
  function_name          = aws_lambda_function.scheduler.function_name
  maximum_retry_attempts = 0
}

resource "aws_iam_role" "monthly_invocation" {
  name = "${var.project_name}-invoke-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnEquals    = { "aws:SourceArn" = aws_scheduler_schedule_group.monthly.arn }
      }
    }]
  })
}

resource "aws_iam_role_policy" "monthly_invocation" {
  name = "InvokeScheduler"
  role = aws_iam_role.monthly_invocation.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.scheduler.arn
    }]
  })
}

resource "aws_scheduler_schedule" "monthly" {
  name                         = "${var.project_name}-monthly"
  group_name                   = aws_scheduler_schedule_group.monthly.name
  schedule_expression          = "cron(0 1 1 * ? *)"
  schedule_expression_timezone = "America/New_York"
  state                        = var.monthly_trigger_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.scheduler.arn
    role_arn = aws_iam_role.monthly_invocation.arn
    input = jsonencode(merge(local.schedule_input, {
      scheduled_at = "<aws.scheduler.scheduled-time>"
    }))

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }

  depends_on = [
    aws_iam_role_policy.monthly_invocation,
    aws_lambda_function_event_invoke_config.scheduler,
  ]
}
