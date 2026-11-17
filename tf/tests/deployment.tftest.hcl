mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/test-role"
    }
  }

  mock_resource "aws_lambda_function" {
    defaults = {
      arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    }
  }
}

run "deployment_contract" {
  command = plan

  variables {
    schedule_name           = "test-input-schedule"
    execution_time          = "10:30:00"
    monthly_trigger_enabled = false
    email = {
      sender     = "sender@example.com"
      recipients = ["recipient@example.com"]
    }
  }

  assert {
    condition = (
      module.scheduler.function.runtime == "python3.12" &&
      module.test_target.function.runtime == "python3.12" &&
      module.test_target.function.function_name == "test-schedule-lambda-dynScheduler"
    )
    error_message = "Both Lambdas must use Python 3.12 and the target must have the requested name."
  }

  assert {
    condition = (
      aws_scheduler_schedule.monthly.schedule_expression == "cron(0 1 1 * ? *)" &&
      aws_scheduler_schedule.monthly.schedule_expression_timezone == "America/New_York" &&
      aws_scheduler_schedule.monthly.state == "DISABLED" &&
      aws_scheduler_schedule.monthly.flexible_time_window[0].mode == "OFF"
    )
    error_message = "The monthly trigger must use 1 AM Eastern and respect the enable input."
  }

  assert {
    condition = (
      jsondecode(aws_scheduler_schedule.monthly.target[0].input).schedule_name == "test-input-schedule" &&
      jsondecode(aws_scheduler_schedule.monthly.target[0].input).execution_time == "10:30:00" &&
      jsondecode(aws_scheduler_schedule.monthly.target[0].input).target_lambda_name == module.test_target.function.function_name &&
      jsondecode(aws_scheduler_schedule.monthly.target[0].input).scheduled_at == "<aws.scheduler.scheduled-time>" &&
      strcontains(aws_scheduler_schedule.monthly.target[0].input, "<aws.scheduler.scheduled-time>") &&
      jsondecode(output.manual_test_payload).bedrock_model_id == "amazon.nova-pro-v1:0" &&
      !contains(keys(jsondecode(output.manual_test_payload)), "scheduled_at")
    )
    error_message = "Inputs must flow into the trigger while manual payloads omit Scheduler placeholders."
  }

  assert {
    condition = (
      module.scheduler.function.environment[0].variables.STATE_TABLE == aws_dynamodb_table.state.name &&
      module.scheduler.function.environment[0].variables.EMAIL_SENDER == "sender@example.com" &&
      jsondecode(module.scheduler.function.environment[0].variables.EMAIL_RECIPIENTS) == ["recipient@example.com"] &&
      module.scheduler.function.environment[0].variables.EMAIL_RECOVERY_POLICY == "manual" &&
      module.scheduler.function.environment[0].variables.ALLOWED_BEDROCK_REGION == "us-east-1"
    )
    error_message = "The scheduler environment must reference deployed resources and email inputs."
  }

  assert {
    condition = (
      module.target_invocation.group.name != module.monthly_invocation.group.name &&
      module.scheduler.maximum_retry_attempts == 0
    )
    error_message = "Schedule groups must be separated and Lambda retries bounded."
  }
}
