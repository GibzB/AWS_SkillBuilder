# example.tfvars - PLACEHOLDERS ONLY. No secrets belong in this file.
#
# Copy to config/my-campaign.tfvars (gitignored) and set YOUR values. The repo is
# public, so keep your real email out of tracked files.
#
#   cp config/example.tfvars config/my-campaign.tfvars
#   ./scripts/deploy.sh <aws-profile>
#
# The Builder Center session secret (not used on the s3 path) is stored only in
# AWS Secrets Manager; only its ARN is referenced here.

campaign_id        = "aws-learning-21"
notification_email = "you@example.com"  # SNS subscription; confirm the email AWS sends
notification_phone = ""                 # optional; empty disables SMS
schedule_time      = "09:00"
timezone           = "Africa/Nairobi"
start_date         = "2026-01-01"
total_days         = 21

# Amazon Nova in eu-west-1 requires an INFERENCE PROFILE (cross-region routing),
# not a bare model ID. The foundation-model ID and routed regions scope IAM to
# exactly the ARNs the profile uses.
aws_region                        = "eu-west-1"
bedrock_model_id                  = "eu.amazon.nova-micro-v1:0"
bedrock_foundation_model_id       = "amazon.nova-micro-v1:0"
bedrock_inference_profile_regions = ["eu-west-1", "eu-west-3", "eu-central-1", "eu-north-1"]

publisher_selection = "s3"  # mock | s3 | builder_center
session_secret_arn  = ""    # not needed on the s3 path
# Only for publisher_selection = "builder_center": reference the Secrets Manager
# secret by ARN (value stored only there), e.g.
#   session_secret_arn = "REPLACE_WITH_SECRET_ARN"
