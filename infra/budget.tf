###############################################################################
# budget.tf
#
# A monthly cost guardrail. This engine should cost well under a few dollars a
# month; the budget exists so a surprise (a runaway loop, a leaked credential, a
# service left running) produces an email long before it becomes a billing
# suspension.
#
# Scope note: the budget covers the WHOLE ACCOUNT, not just these resources.
# Filtering by the Project tag only works after that tag is activated as a cost
# allocation tag in the Billing console (and takes up to ~24h to take effect);
# until then a tag-filtered budget would silently report zero. An account-wide
# budget is the safer default for a guardrail.
###############################################################################

resource "aws_budgets_budget" "monthly" {
  count = var.monthly_budget_usd > 0 ? 1 : 0

  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
  }

  # Early warning: projected month-end spend exceeds the limit.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.notification_email]
  }

  tags = local.common_tags
}
