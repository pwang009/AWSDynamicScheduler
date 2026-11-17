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

module "test_target" {
  source           = "./modules/lambda_function"
  function_name    = local.target_name
  role_name        = "${var.project_name}-test-lambda"
  policy_name      = "WriteTestLogs"
  handler          = "app.lambda_handler"
  filename         = data.archive_file.test_target.output_path
  source_code_hash = data.archive_file.test_target.output_base64sha256
  timeout          = 10
  memory_size      = 128
}

module "target_invocation" {
  source      = "./modules/scheduler_invocation"
  group_name  = "${var.project_name}-generated"
  role_name   = "${var.project_name}-invoke-target"
  policy_name = "InvokeTestTarget"
  account_id  = data.aws_caller_identity.current.account_id
  target_arn  = module.test_target.function.arn
}

module "scheduler" {
  source                 = "./modules/lambda_function"
  function_name          = local.scheduler_name
  role_name              = "${var.project_name}-lambda"
  policy_name            = "ManageDynamicSchedules"
  handler                = "handler.lambda_handler"
  filename               = data.archive_file.scheduler.output_path
  source_code_hash       = data.archive_file.scheduler.output_base64sha256
  timeout                = 120
  memory_size            = 256
  maximum_retry_attempts = 0

  additional_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Effect   = "Allow"
        Action   = ["scheduler:CreateSchedule", "scheduler:GetSchedule", "scheduler:DeleteSchedule"]
        Resource = "arn:${data.aws_partition.current.partition}:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/${module.target_invocation.group.name}/*"
      },
      {
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = module.target_invocation.role_arn
        Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } }
      },
      {
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = "arn:${data.aws_partition.current.partition}:bedrock:${var.bedrock_region}::foundation-model/${var.bedrock_model_id}"
      }
    ]
  })
  environment_variables = {
    STATE_TABLE              = aws_dynamodb_table.state.name
    SCHEDULE_GROUP           = module.target_invocation.group.name
    TARGET_LAMBDA_ARN        = module.test_target.function.arn
    SCHEDULER_ROLE_ARN       = module.target_invocation.role_arn
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

module "monthly_invocation" {
  source      = "./modules/scheduler_invocation"
  group_name  = "${var.project_name}-monthly"
  role_name   = "${var.project_name}-invoke-scheduler"
  policy_name = "InvokeScheduler"
  account_id  = data.aws_caller_identity.current.account_id
  target_arn  = module.scheduler.function.arn
}

resource "aws_scheduler_schedule" "monthly" {
  name                         = "${var.project_name}-monthly"
  group_name                   = module.monthly_invocation.group.name
  schedule_expression          = "cron(0 1 1 * ? *)"
  schedule_expression_timezone = "America/New_York"
  state                        = var.monthly_trigger_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.scheduler.function.arn
    role_arn = module.monthly_invocation.role_arn
    input = replace(replace(jsonencode(merge(local.schedule_input, {
      scheduled_at = "<aws.scheduler.scheduled-time>"
    })), "\\u003c", "<"), "\\u003e", ">")

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}
