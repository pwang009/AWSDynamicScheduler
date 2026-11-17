variable "project_name" {
  description = "Prefix for scheduler infrastructure. The test target has its own fixed name."
  type        = string
  default     = "dynamic-lambda-scheduler"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9-]{0,39}$", var.project_name))
    error_message = "Use 1-40 letters, digits, or hyphens, starting with a letter."
  }
}

variable "aws_region" {
  description = "Commercial AWS deployment Region."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "This deployment currently supports commercial us-east-1 only."
  }
}

variable "schedule_name" {
  description = "Generated schedule name. An existing same-name schedule in the generated group is replaced."
  type        = string
  default     = "example-monthly-schedule"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]{1,64}$", var.schedule_name))
    error_message = "Use 1-64 letters, digits, underscores, dots, or hyphens."
  }
}

variable "schedule_description" {
  description = "One monthly description for the deployed test target."
  type        = string
  default     = "the Friday after the 1st Sunday"

  validation {
    condition     = length(trimspace(var.schedule_description)) > 0 && length(var.schedule_description) <= 4000
    error_message = "Provide a nonempty description of at most 4000 characters."
  }
}

variable "execution_time" {
  description = "Target execution time in the input timezone, HH:MM:SS."
  type        = string
  default     = "09:00:00"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$", var.execution_time))
    error_message = "Provide a valid 24-hour HH:MM:SS time."
  }
}

variable "timezone" {
  description = "IANA timezone for target execution. Python validates the actual identifier."
  type        = string
  default     = "America/New_York"

  validation {
    condition     = length(trimspace(var.timezone)) > 0
    error_message = "Provide an IANA timezone identifier."
  }
}

variable "bedrock_model_id" {
  description = "Approved Bedrock model passed in the event and restricted in IAM."
  type        = string
  default     = "amazon.nova-pro-v1:0"

  validation {
    condition     = var.bedrock_model_id == "amazon.nova-pro-v1:0"
    error_message = "The initial deployment supports Amazon Nova Pro only."
  }
}

variable "bedrock_region" {
  description = "Region used for Bedrock inference, independent of Scheduler configuration."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.bedrock_region == "us-east-1"
    error_message = "The approved Bedrock Region is us-east-1."
  }
}

variable "monthly_trigger_enabled" {
  description = "Whether to enable the monthly 1:00 AM Eastern trigger. Disable for manual-only tests."
  type        = bool
  default     = true
}

variable "email" {
  description = "Environment settings only; the handler email import/call remain commented out."
  type = object({
    provider           = optional(string, "ses")
    sender             = optional(string, "")
    recipients         = optional(list(string), [])
    max_attempts       = optional(number, 3)
    retry_base_seconds = optional(number, 1)
    recovery_policy    = optional(string, "manual")
  })
  default = {}

  validation {
    condition = (
      var.email.provider == "ses" &&
      var.email.recovery_policy == "manual" &&
      var.email.max_attempts >= 1 && var.email.max_attempts <= 3 &&
      floor(var.email.max_attempts) == var.email.max_attempts &&
      var.email.retry_base_seconds >= 0 && var.email.retry_base_seconds <= 2
    )
    error_message = "Email supports ses/manual, 1-3 total attempts, and a 0-2 second retry base."
  }
}
