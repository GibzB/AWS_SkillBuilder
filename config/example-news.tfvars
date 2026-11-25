# example-news.tfvars - PLACEHOLDERS ONLY. No secrets belong in this file.
#
# Config for the AWS news campaign stack (aws-news-30). Copy it, set YOUR email,
# and deploy into its own Terraform workspace so the old campaign stays untouched:
#
#   cp config/example-news.tfvars config/news-campaign.tfvars
#   scripts/deploy.sh <aws-profile> --tfvars config/news-campaign.tfvars \
#     --workspace-suffix aws-news-30 --email YOUR_EMAIL --plan-only
#
# The repo is public, so keep your real email out of tracked files.
campaign_id        = "aws-news-30"
notification_email = "REPLACE_WITH_EMAIL"  # set via --email; AWS then sends a subscription to confirm
notification_phone = ""                 # optional; empty disables SMS
schedule_time      = "09:00"
timezone           = "Africa/Nairobi"
start_date         = "2026-01-01"       # set to the real first day before deploying
total_days         = 30

monthly_budget_usd     = 8
lambda_timeout_seconds = 600

# Sunday roundup of the week's daily articles.
weekly_roundup_enabled = true
weekly_roundup_time    = "18:00"

aws_region                        = "eu-west-1"
bedrock_model_id                  = "eu.amazon.nova-pro-v1:0"
bedrock_foundation_model_id       = "amazon.nova-pro-v1:0"
bedrock_inference_profile_regions = ["eu-west-1", "eu-west-3", "eu-central-1", "eu-north-1"]

publisher_selection = "s3"  # mock | s3 | builder_center
session_secret_arn  = ""    # not needed on the s3 path
