###############################################################################
# articles.tf
#
# Article hosting for the S3 publisher (publisher_selection = "s3"):
# a PRIVATE S3 bucket served over HTTPS by CloudFront using Origin Access
# Control (OAC). The bucket blocks all public access; only the CloudFront
# distribution can read it, via a bucket policy scoped to the distribution ARN.
#
# All resources are gated with count on the s3 selection, so the mock path
# provisions nothing here and incurs no cost. Everything sits comfortably in
# the AWS free tier for this volume (a few dozen small objects, low requests).
###############################################################################

locals {
  articles_enabled = var.publisher_selection == "s3" ? 1 : 0
  articles_bucket  = "${local.name_prefix}-articles"
}

# --- Private S3 bucket --------------------------------------------------------

resource "aws_s3_bucket" "articles" {
  count         = local.articles_enabled
  bucket        = local.articles_bucket
  force_destroy = true # allow teardown even with objects present
  tags          = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "articles" {
  count                   = local.articles_enabled
  bucket                  = aws_s3_bucket.articles[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "articles" {
  count  = local.articles_enabled
  bucket = aws_s3_bucket.articles[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# --- CloudFront Origin Access Control (OAC, the modern replacement for OAI) ---

resource "aws_cloudfront_origin_access_control" "articles" {
  count                             = local.articles_enabled
  name                              = "${local.name_prefix}-articles-oac"
  description                       = "OAC for the private articles bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# --- CloudFront distribution --------------------------------------------------

resource "aws_cloudfront_distribution" "articles" {
  count               = local.articles_enabled
  enabled             = true
  default_root_object = "index.html"
  comment             = "${local.name_prefix} articles"
  price_class         = "PriceClass_100" # cheapest edge footprint (NA + EU)
  tags                = local.common_tags

  origin {
    domain_name              = aws_s3_bucket.articles[0].bucket_regional_domain_name
    origin_id                = "articles-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.articles[0].id
  }

  default_cache_behavior {
    target_origin_id       = "articles-s3"
    viewer_protocol_policy = "redirect-to-https" # HTTPS enforced
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 300
    max_ttl     = 3600
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # *.cloudfront.net HTTPS cert
  }
}

# --- Bucket policy: allow ONLY this CloudFront distribution to read ----------

data "aws_iam_policy_document" "articles_bucket" {
  count = local.articles_enabled

  statement {
    sid    = "AllowCloudFrontOACRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = [
      "${aws_s3_bucket.articles[0].arn}/*",
    ]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.articles[0].arn]
    }
  }
}

resource "aws_s3_bucket_policy" "articles" {
  count  = local.articles_enabled
  bucket = aws_s3_bucket.articles[0].id
  policy = data.aws_iam_policy_document.articles_bucket[0].json
}

# --- IAM: let the Orchestrator Lambda write article objects -------------------
# Scoped to PutObject on the articles bucket only (least privilege, Req 8.7).

data "aws_iam_policy_document" "articles_put" {
  count = local.articles_enabled

  statement {
    sid    = "ArticlesPutObject"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = [
      "${aws_s3_bucket.articles[0].arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "articles_put" {
  count  = local.articles_enabled
  name   = "${local.name_prefix}-articles-put"
  role   = aws_iam_role.orchestrator.id
  policy = data.aws_iam_policy_document.articles_put[0].json
}
