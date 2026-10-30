###############################################################################
# variables.tf
#
# Configuration schema for the AWS Builder Learning Automation Engine.
#
# Exposes campaign identifier, notification email, notification phone, schedule,
# timezone, Bedrock model, AWS region, notification topic, and publishing
# configuration (Requirement 19.2). Secrets are NEVER declared here as values;
# only a Secrets Manager ARN reference is accepted (Requirements 8.1, 19.3).
#
# Validation blocks surface invalid configuration at plan/apply time, naming the
# offending field and its expected format (Requirements 19.4, 19.5).
###############################################################################

variable "aws_region" {
  description = "AWS region into which the engine is deployed."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier, for example us-east-1."
  }
}

variable "campaign_id" {
  description = "Stable campaign identifier used to name and correlate resources."
  type        = string

  validation {
    condition     = length(trimspace(var.campaign_id)) >= 1 && length(var.campaign_id) <= 64
    error_message = "campaign_id must be a non-empty string of at most 64 characters."
  }

  validation {
    condition     = can(regex("^[a-zA-Z0-9-]+$", var.campaign_id))
    error_message = "campaign_id must contain only letters, numbers, and hyphens (no spaces or underscores)."
  }
}

variable "notification_email" {
  description = "Email address subscribed to the SNS notification topic."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, for example you@example.com."
  }
}

variable "notification_phone" {
  description = "Optional E.164 phone number for SMS notifications. Empty string disables SMS."
  type        = string
  default     = ""

  validation {
    condition     = var.notification_phone == "" || can(regex("^\\+[1-9][0-9]{6,14}$", var.notification_phone))
    error_message = "notification_phone must be empty or a valid E.164 number, for example +254700000000."
  }
}

variable "schedule_time" {
  description = "Daily execution time in 24-hour HH:MM format, evaluated in the campaign timezone."
  type        = string
  default     = "09:00"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]$", var.schedule_time))
    error_message = "schedule_time must be in 24-hour HH:MM format, for example 09:00."
  }
}

variable "timezone" {
  description = "IANA timezone used to evaluate the schedule (for example Africa/Nairobi)."
  type        = string
  default     = "Africa/Nairobi"

  validation {
    condition     = can(regex("^[A-Za-z]+/[A-Za-z_]+$", var.timezone))
    error_message = "timezone must be an IANA timezone identifier, for example Africa/Nairobi."
  }
}

variable "start_date" {
  description = "Campaign start date in ISO-8601 date format (YYYY-MM-DD)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.start_date))
    error_message = "start_date must be an ISO-8601 date, for example 2026-01-01."
  }
}

variable "scheduler_start_date" {
  description = <<-EOT
    Optional explicit EventBridge Scheduler StartDate (RFC3339, e.g.
    2026-10-01T00:00:00Z). Leave empty to derive it automatically: the campaign
    start is used only when it is in the future, otherwise StartDate is omitted
    and the schedule fires on the next cron occurrence. EventBridge rejects a
    StartDate earlier than ~5 minutes ago.
  EOT
  type        = string
  default     = ""

  validation {
    condition = (
      var.scheduler_start_date == "" ||
      can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.scheduler_start_date))
    )
    error_message = "scheduler_start_date must be empty or an RFC3339 UTC instant, for example 2026-10-01T00:00:00Z."
  }
}

variable "total_days" {
  description = "Total number of campaign days. The engine is reusable for any length in range."
  type        = number
  default     = 21

  validation {
    condition     = var.total_days >= 1 && var.total_days <= 365 && floor(var.total_days) == var.total_days
    error_message = "total_days must be a whole number between 1 and 365 inclusive."
  }
}

variable "bedrock_model_id" {
  description = <<-EOT
    Amazon Bedrock model or inference-profile identifier used for generation.
    In regions where a model requires cross-region routing (for example Amazon
    Nova in eu-west-1), use the inference-profile ID such as
    eu.amazon.nova-micro-v1:0 rather than the bare model ID.
  EOT
  type        = string
  default     = "eu.amazon.nova-micro-v1:0"

  validation {
    condition     = length(trimspace(var.bedrock_model_id)) >= 1
    error_message = "bedrock_model_id must be a non-empty Bedrock model or inference-profile identifier."
  }
}

variable "bedrock_foundation_model_id" {
  description = <<-EOT
    The underlying foundation-model ID that bedrock_model_id resolves to (used
    to scope IAM to the foundation-model ARNs an inference profile routes to).
    When bedrock_model_id is a bare model ID this equals it.
  EOT
  type        = string
  default     = "amazon.nova-micro-v1:0"

  validation {
    condition     = length(trimspace(var.bedrock_foundation_model_id)) >= 1
    error_message = "bedrock_foundation_model_id must be a non-empty foundation-model identifier."
  }
}

variable "bedrock_inference_profile_regions" {
  description = <<-EOT
    Regions whose foundation-model ARNs an inference profile may route to, so
    IAM can allow InvokeModel on each. For the EU Nova profile these are
    eu-west-1, eu-west-3, eu-central-1, and eu-north-1. Empty means the model
    is invoked directly in aws_region with no cross-region routing.
  EOT
  type        = list(string)
  default     = ["eu-west-1", "eu-west-3", "eu-central-1", "eu-north-1"]
}

variable "publisher_selection" {
  description = "Which publishing adapter to use. Defaults to mock so the MVP stays under USD 5."
  type        = string
  default     = "mock"

  validation {
    condition     = contains(["mock", "builder_center", "s3"], var.publisher_selection)
    error_message = "publisher_selection must be one of mock, builder_center, or s3."
  }
}

variable "session_secret_arn" {
  description = <<-EOT
    ARN of the Secrets Manager secret holding Builder Center session material.
    Only the ARN is referenced here; the secret VALUE is stored exclusively in
    Secrets Manager and never in source control (Requirements 8.1, 8.2, 19.3).
  EOT
  type        = string
  default     = ""

  validation {
    # Accept: empty (mock path, no secret needed), the documented placeholder
    # token from example.tfvars, or a real Secrets Manager ARN. Deployment-time
    # completeness for the builder_center path is enforced by a later task.
    condition = (
      var.session_secret_arn == "" ||
      var.session_secret_arn == "REPLACE_WITH_SECRET_ARN" ||
      can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:", var.session_secret_arn))
    )
    error_message = "session_secret_arn must be empty, the placeholder REPLACE_WITH_SECRET_ARN, or a Secrets Manager ARN (arn:aws:secretsmanager:...)."
  }
}

variable "log_retention_days" {
  description = "Retention period for the Lambda CloudWatch log group."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a CloudWatch Logs supported retention value."
  }
}

variable "lambda_timeout_seconds" {
  description = "Lambda execution timeout. Must accommodate generation (120s) and publication (60s)."
  type        = number
  default     = 300

  validation {
    condition     = var.lambda_timeout_seconds >= 120 && var.lambda_timeout_seconds <= 900
    error_message = "lambda_timeout_seconds must be between 120 and 900 seconds."
  }
}

variable "tags" {
  description = "Common tags applied to all taggable resources."
  type        = map(string)
  default     = {}
}
