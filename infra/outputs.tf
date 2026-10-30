###############################################################################
# outputs.tf
#
# Non-sensitive outputs describing the deployed engine topology. No secret
# values are exposed here (Requirements 8.1, 19.3).
###############################################################################

output "dynamodb_table_name" {
  description = "Name of the single-table DynamoDB store."
  value       = aws_dynamodb_table.campaign.name
}

output "dynamodb_table_arn" {
  description = "ARN of the single-table DynamoDB store."
  value       = aws_dynamodb_table.campaign.arn
}

output "sns_topic_arn" {
  description = "ARN of the notifications SNS topic."
  value       = aws_sns_topic.notifications.arn
}

output "orchestrator_function_name" {
  description = "Name of the Orchestrator Lambda function."
  value       = aws_lambda_function.orchestrator.function_name
}

output "orchestrator_function_arn" {
  description = "ARN of the Orchestrator Lambda function."
  value       = aws_lambda_function.orchestrator.arn
}

output "command_api_url" {
  description = "IAM-authenticated Lambda Function URL for the Command API."
  value       = aws_lambda_function_url.command_api.function_url
}

output "orchestrator_role_arn" {
  description = "ARN of the least-privilege Orchestrator execution role."
  value       = aws_iam_role.orchestrator.arn
}

output "schedule_name" {
  description = "Name of the EventBridge Scheduler daily schedule."
  value       = aws_scheduler_schedule.daily.name
}

output "log_group_name" {
  description = "CloudWatch log group for the Orchestrator Lambda."
  value       = aws_cloudwatch_log_group.orchestrator.name
}

output "articles_base_url" {
  description = "CloudFront HTTPS base URL serving published articles (s3 publisher only)."
  value       = var.publisher_selection == "s3" ? "https://${aws_cloudfront_distribution.articles[0].domain_name}" : ""
}

output "articles_bucket" {
  description = "Private S3 bucket holding article HTML (s3 publisher only)."
  value       = var.publisher_selection == "s3" ? aws_s3_bucket.articles[0].bucket : ""
}
