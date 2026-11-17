variable "function_name" {
  type = string
}

variable "role_name" {
  type = string
}

variable "policy_name" {
  type = string
}

variable "handler" {
  type = string
}

variable "filename" {
  type = string
}

variable "source_code_hash" {
  type = string
}

variable "timeout" {
  type = number
}

variable "memory_size" {
  type = number
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "additional_policy_json" {
  description = "Additional IAM statements; CloudWatch log permissions are included by the module."
  type        = string
  default     = "{\"Statement\":[]}"
}

variable "maximum_retry_attempts" {
  description = "Null leaves the function's asynchronous invocation configuration unmanaged."
  type        = number
  default     = null
}
