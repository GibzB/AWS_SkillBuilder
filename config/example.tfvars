# example.tfvars - PLACEHOLDERS ONLY. No secrets belong in this file.
#
# The Builder Center session secret is stored exclusively in AWS Secrets Manager;
# only its ARN is referenced here (Requirements 8.1, 8.2, 19.3).
#
# Copy to a working tfvars file and replace placeholders before deploying:
#   terraform apply -var-file=../config/example.tfvars

campaign_id         = "aws-learning-21"
notification_email  = "you@example.com"
notification_phone  = ""                         # optional; empty disables SMS
schedule_time       = "09:00"
timezone            = "Africa/Nairobi"
start_date          = "2026-01-01"
total_days          = 21

# Amazon Nova in eu-west-1 requires an INFERENCE PROFILE (cross-region routing),
# not a bare model ID. Use the eu. profile ID; the foundation-model ID and the
# routed regions below scope IAM to exactly the ARNs the profile uses.
aws_region                        = "eu-west-1"
bedrock_model_id                  = "eu.amazon.nova-micro-v1:0"
bedrock_foundation_model_id       = "amazon.nova-micro-v1:0"
bedrock_inference_profile_regions = ["eu-west-1", "eu-west-3", "eu-central-1", "eu-north-1"]

publisher_selection = "mock"                     # mock | builder_center
session_secret_arn  = "REPLACE_WITH_SECRET_ARN"  # value stored only in Secrets Manager
