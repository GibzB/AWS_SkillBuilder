###############################################################################
# main.tf
#
# Minimal but declared scaffolding for the AWS Builder Learning Automation
# Engine (Requirements 18.1, 18.2, 18.3, 19.1).
#
# Design decisions realized here:
#   - Serverless, event-triggered only. No always-on compute (Req 18.1, 18.3).
#   - DynamoDB single-table design, on-demand / PAY_PER_REQUEST (Req 18.2).
#   - Lambda + Function URL with AWS_IAM auth (design API decision, Req 11.6/11.7).
#   - EventBridge Scheduler, timezone-aware (Scheduling section, Req 11.1/11.2).
#   - SNS topic for notifications (Req 10.x).
#   - Least-privilege IAM scoped separately per service (Security and IAM, Req 8.7).
#   - CloudWatch log group for the Lambda (Req 17.x).
#
# Resources are intentionally minimal: this scaffolds the topology so later
# tasks (12.5) can complete wiring and packaging.
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name_prefix = "aws-builder-${var.campaign_id}"

  common_tags = merge(
    {
      Project    = "aws-builder-automation"
      CampaignId = var.campaign_id
      ManagedBy  = "terraform"
    },
    var.tags,
  )

  lambda_handler       = "app.orchestrator.handler.lambda_handler"
  lambda_runtime       = "python3.12"
  lambda_function_name = "${local.name_prefix}-orchestrator"

  # EventBridge Scheduler StartDate. The campaign's logical start_date may be in
  # the past (an already-running campaign), but EventBridge rejects a past
  # StartDate. Resolve to: an explicit override if given, else the campaign
  # start midnight UTC only when it is in the future, else null (omit StartDate
  # so the schedule fires on the next cron occurrence).
  campaign_start_instant = "${var.start_date}T00:00:00Z"
  scheduler_start_date = (
    var.scheduler_start_date != "" ? var.scheduler_start_date :
    timecmp(local.campaign_start_instant, timestamp()) > 0 ? local.campaign_start_instant :
    null
  )
}

###############################################################################
# DynamoDB - single-table design, on-demand capacity (Requirement 18.2)
###############################################################################

resource "aws_dynamodb_table" "campaign" {
  name         = "${local.name_prefix}-campaign"
  billing_mode = "PAY_PER_REQUEST" # on-demand: no provisioned/always-on capacity (Req 18.2)

  # Attribute names are lowercase pk/sk to match the store's single-table
  # marshalling (app/campaign/store.py) and the design Data Models section.
  # DynamoDB attribute names are case-sensitive, so these must match exactly.
  hash_key  = "pk"
  range_key = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

###############################################################################
# SNS - notification topic (Requirement 10.x)
###############################################################################

resource "aws_sns_topic" "notifications" {
  name = "${local.name_prefix}-notifications"
  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

###############################################################################
# CloudWatch Logs - Lambda log group (Requirement 17.x)
###############################################################################

resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/aws/lambda/${local.lambda_function_name}"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

###############################################################################
# IAM - least-privilege, scoped separately per service (Requirement 8.7)
###############################################################################

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "orchestrator" {
  name               = "${local.name_prefix}-orchestrator-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = local.common_tags
}

# --- DynamoDB access scoped to the single table ARN only ---------------------
data "aws_iam_policy_document" "dynamodb" {
  statement {
    sid    = "DynamoDBTableAccess"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [
      aws_dynamodb_table.campaign.arn,
    ]
  }
}

resource "aws_iam_role_policy" "dynamodb" {
  name   = "${local.name_prefix}-dynamodb"
  role   = aws_iam_role.orchestrator.id
  policy = data.aws_iam_policy_document.dynamodb.json
}

# --- Bedrock InvokeModel scoped to the profile + its routed model ARNs -------
# Invoking through an inference profile (required for Nova in several regions,
# e.g. eu-west-1) needs InvokeModel permission on BOTH the inference-profile
# ARN in this account/region AND each foundation-model ARN the profile routes
# to across regions. We scope to exactly those ARNs (no wildcards): the profile
# ARN plus one foundation-model ARN per configured routing region. When no
# routing regions are configured we fall back to the model ARN in this region.
locals {
  bedrock_is_inference_profile = length(var.bedrock_inference_profile_regions) > 0

  bedrock_foundation_model_arns = local.bedrock_is_inference_profile ? [
    for region in var.bedrock_inference_profile_regions :
    "arn:aws:bedrock:${region}::foundation-model/${var.bedrock_foundation_model_id}"
    ] : [
    "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/${var.bedrock_model_id}"
  ]

  bedrock_inference_profile_arns = local.bedrock_is_inference_profile ? [
    "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_model_id}"
  ] : []

  bedrock_invoke_resources = concat(
    local.bedrock_foundation_model_arns,
    local.bedrock_inference_profile_arns,
  )
}

data "aws_iam_policy_document" "bedrock" {
  statement {
    sid    = "BedrockInvokeModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
    ]
    resources = local.bedrock_invoke_resources
  }
}

resource "aws_iam_role_policy" "bedrock" {
  name   = "${local.name_prefix}-bedrock"
  role   = aws_iam_role.orchestrator.id
  policy = data.aws_iam_policy_document.bedrock.json
}

# --- Secrets Manager GetSecretValue scoped to the specific secret ARN --------
# Only attached when a session secret ARN is configured (builder_center path).
data "aws_iam_policy_document" "secrets" {
  count = var.session_secret_arn == "" ? 0 : 1

  statement {
    sid    = "SecretsManagerRead"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      var.session_secret_arn,
    ]
  }
}

resource "aws_iam_role_policy" "secrets" {
  count  = var.session_secret_arn == "" ? 0 : 1
  name   = "${local.name_prefix}-secrets"
  role   = aws_iam_role.orchestrator.id
  policy = data.aws_iam_policy_document.secrets[0].json
}

# --- SNS Publish scoped to the specific topic ARN only -----------------------
data "aws_iam_policy_document" "sns" {
  statement {
    sid    = "SNSPublish"
    effect = "Allow"
    actions = [
      "sns:Publish",
    ]
    resources = [
      aws_sns_topic.notifications.arn,
    ]
  }
}

resource "aws_iam_role_policy" "sns" {
  name   = "${local.name_prefix}-sns"
  role   = aws_iam_role.orchestrator.id
  policy = data.aws_iam_policy_document.sns.json
}

# --- CloudWatch Logs scoped to the function log group only -------------------
data "aws_iam_policy_document" "logs" {
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.orchestrator.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "logs" {
  name   = "${local.name_prefix}-logs"
  role   = aws_iam_role.orchestrator.id
  policy = data.aws_iam_policy_document.logs.json
}

###############################################################################
# Lambda - Orchestrator function (Requirements 18.1, 18.3, 19.1)
#
# The deployment artifact is built by scripts/build_lambda.sh, which stages the
# application package (app/) plus its runtime dependencies into
# build/lambda_src/. Terraform zips that staged directory here, so the source
# hash changes only when packaged content changes and a redeploy happens only
# then. Run the build script before plan/apply:
#
#     scripts/build_lambda.sh
#
# The build directory must exist at plan time; a guard fails with a clear
# message if the build step was skipped.
###############################################################################

resource "null_resource" "lambda_build_guard" {
  triggers = {
    build_present = fileexists("${path.module}/build/lambda_src/app/orchestrator/handler.py") ? "yes" : "no"
  }

  lifecycle {
    precondition {
      condition     = fileexists("${path.module}/build/lambda_src/app/orchestrator/handler.py")
      error_message = "Lambda build directory missing. Run scripts/build_lambda.sh before terraform plan/apply."
    }
  }
}

data "archive_file" "orchestrator" {
  type        = "zip"
  source_dir  = "${path.module}/build/lambda_src"
  output_path = "${path.module}/build/lambda.zip"

  depends_on = [null_resource.lambda_build_guard]
}

resource "aws_lambda_function" "orchestrator" {
  function_name = local.lambda_function_name
  role          = aws_iam_role.orchestrator.arn
  handler       = local.lambda_handler
  runtime       = local.lambda_runtime
  timeout       = var.lambda_timeout_seconds

  filename         = data.archive_file.orchestrator.output_path
  source_code_hash = data.archive_file.orchestrator.output_base64sha256

  environment {
    variables = {
      CAMPAIGN_ID         = var.campaign_id
      TABLE_NAME          = aws_dynamodb_table.campaign.name
      SNS_TOPIC_ARN       = aws_sns_topic.notifications.arn
      BEDROCK_MODEL_ID    = var.bedrock_model_id
      TIMEZONE            = var.timezone
      TOTAL_DAYS          = tostring(var.total_days)
      PUBLISHER_SELECTION = var.publisher_selection
      # Only the ARN is referenced. The secret value is resolved at runtime from
      # Secrets Manager and never stored in plaintext (Requirements 8.1, 8.3).
      SESSION_SECRET_ARN = var.session_secret_arn
      # S3 publisher target: the private articles bucket and its CloudFront
      # HTTPS base URL. Empty on the mock path.
      ARTICLE_BUCKET   = var.publisher_selection == "s3" ? aws_s3_bucket.articles[0].bucket : ""
      ARTICLE_BASE_URL = var.publisher_selection == "s3" ? "https://${aws_cloudfront_distribution.articles[0].domain_name}" : ""
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.orchestrator,
    aws_iam_role_policy.logs,
  ]

  tags = local.common_tags
}

# Lambda Function URL with IAM (SigV4) auth (design API decision, Req 11.6/11.7)
resource "aws_lambda_function_url" "command_api" {
  function_name      = aws_lambda_function.orchestrator.function_name
  authorization_type = "AWS_IAM"
}

###############################################################################
# EventBridge Scheduler - timezone-aware daily trigger (Req 11.1, 11.2, 18.3)
###############################################################################

data "aws_iam_policy_document" "scheduler_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name_prefix}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    sid       = "InvokeOrchestrator"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.orchestrator.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name   = "${local.name_prefix}-scheduler-invoke"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "daily" {
  name = "${local.name_prefix}-daily"

  flexible_time_window {
    mode = "OFF"
  }

  # Evaluate the configured HH:MM in the campaign timezone (Req 11.2).
  schedule_expression          = "cron(${split(":", var.schedule_time)[1]} ${split(":", var.schedule_time)[0]} * * ? *)"
  schedule_expression_timezone = var.timezone

  # The campaign's logical start_date lives in DynamoDB; it may legitimately be
  # in the past (a campaign already under way). EventBridge Scheduler, however,
  # rejects a StartDate earlier than ~5 minutes ago. So we only set the
  # scheduler StartDate when the campaign start is in the future; otherwise we
  # omit it and the schedule begins firing on the next cron occurrence, which
  # is the desired behavior for an already-started or immediately-starting
  # campaign. An explicit override is available via scheduler_start_date.
  start_date = local.scheduler_start_date

  target {
    arn      = aws_lambda_function.orchestrator.arn
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      source      = "eventbridge-scheduler"
      campaign_id = var.campaign_id
    })
  }
}
