# Recovered Build Prompt — AWS Builder 21-Day Automation Engine

> Recovered from a stuck Kiro session (`ce2d51ac-...`) whose sub-agent call never
> returned. The assistant only ever replied "On it." and produced no output.
> This file preserves the original build prompt verbatim so it is not lost.
> `prd.md` remains the authoritative product requirements document.

---

Kiro Build Prompt — AWS Builder 21-Day Automation Engine

You are the lead cloud architect, senior AWS engineer, security engineer, and implementation engineer for this project.

The repository contains a prd.md file. Treat prd.md as the authoritative product requirements document.

Your job is to take this project from PRD -> technical design -> implementation -> testing -> deployment-ready MVP.

Do NOT immediately start writing large amounts of code. First inspect the repository and prd.md, understand the requirements, identify technical risks, and create a practical implementation plan.

## 1. Core Objective
Build the AWS Builder 21-Day Learning Automation Engine described in prd.md. Define a learning campaign around AWS fundamentals, generate daily first-person educational AWS Builder articles using Amazon Bedrock, validate them, publish them to AWS Builder Center through a secure publishing adapter, and notify via Amazon SNS. Initial campaign is 21 days. Architecture must be reusable for future 21/30/60/90-day or custom series. Not hard-coded for the first campaign.

## 2. Product Principles
MVP simplicity. No unnecessary microservices, containers, Kubernetes, extra databases, frontend frameworks, queues, orchestration platforms, or third-party services. Prefer AWS-native serverless: Lambda, EventBridge Scheduler, DynamoDB, Bedrock, SNS, Secrets Manager, CloudWatch, API Gateway HTTP API OR authenticated Lambda Function URL, IAM. S3 only where clearly needed.

## 3. Critical Builder Center Requirement
Publishing is the highest-risk part. Do NOT assume an official public publishing API exists. Investigate available Builder Center mechanisms and required auth/session behavior. Builder ID is the credential. MUST NOT: store the password, put credentials in source/env vars, log passwords, bypass MFA/CAPTCHA/bot protection/rate limiting, defeat security controls, harvest credentials, or use undocumented bypasses. Isolate any browser automation behind a publishing adapter: `publishArticle(article) -> PublicationResult`. If it cannot be automated safely from Lambda, STOP and document the blocker.

## 4. Authentication Architecture
Manually authenticate once; persist session material securely if possible; Lambda uses the secure session; no daily password entry. Never store the password. Use Secrets Manager for session material. Least privilege. On session expiry, detect and notify rather than bypass.

## 5. AI Architecture
Bedrock is a LIGHT generation layer, not an autonomous agent. Input a structured daily topic; output `{title, description, body_markdown, tags}`. Validate deterministically. Never invent personal experiences, employment, certifications, customer info, incidents, deployed resources, results, cost savings, screenshots, or measurements. First-person authentic learning voice. Technically useful without exaggeration.

## 6. Hack Generation
System proposes the sequence; user approves once; daily execution then automatic. First campaign covers AWS fundamentals (account/security mindset, IAM, S3, S3 lifecycle, EC2, security groups, VPC, subnets/routes, Lambda, API Gateway, EventBridge, CloudWatch, Systems Manager, DynamoDB, RDS, SNS, IaC, security fundamentals, cost optimization, serverless, lessons learned). Examples, not hard-coded. Support arbitrary topics. Never actually provision AWS resources for hacks. Articles may include CLI/Terraform/CloudFormation/JSON/YAML/ASCII diagrams but must distinguish examples from executed actions.

## 7. Markdown / Diagrams
No cover images in MVP. Prefer portable Markdown and ASCII/text diagrams. Do not depend on Mermaid unless Builder Center compatibility is verified.

## 8. Campaign State
Campaign: DRAFT, APPROVED, READY, RUNNING, PAUSED, COMPLETED. Day: PLANNED, GENERATING, GENERATED, READY, PUBLISHING, PUBLISHED, FAILED. No invalid transitions. Document rules.

## 9. Idempotency
Mandatory. EventBridge may double-invoke; manual RUN may overlap. Never publish twice. Deterministic key on `campaign_id + day`. Check DynamoDB before publishing; if PUBLISHED, return idempotent success. Use conditional writes.

## 10. API
Minimal authenticated API: POST /campaign, /campaign/{id}/approve, /run, /retry, GET /campaign/{id}/status, /pause, /resume. Consider authenticated Lambda Function URL if API Gateway adds cost/complexity. No unauthenticated RUN endpoint. Manual RUN without infra changes.

## 11. EventBridge
EventBridge Scheduler for scheduled execution. Configurable time, timezone, start date, duration. Default timezone Africa/Nairobi. No always-on Lambda.

## 12. DynamoDB
Clean schema for campaigns, days, article metadata + state, publication status/URL, retry count, errors, timestamps, idempotency. Document PK, SK, GSIs, access patterns, conditional writes, TTL. Avoid overengineering.

## 13. Bedrock
Cost-effective model for short technical articles; no large model unless proven necessary. Minimize tokens. Versioned prompt artifacts, not inline giant prompts. Structured output, response validation, retry, malformed-JSON handling, timeout handling, cost awareness. No infinite retries.

## 14. Content Validation
Deterministic validator. Reject if: missing/oversized title (>255), missing/oversized description (>512), missing body, missing tags, >5 tags, placeholder text, missing structure, fabricated personal claims, or topic mismatch. Return `{valid, errors[]}`.

## 15. SNS Notifications
Email; SMS where configured. Success: campaign, day, title, URL, timestamp. Failure: campaign, day, failed stage, sanitized error, retry recommendation. Never include secrets/cookies/tokens. Notification failure must not fail an already-successful publication.

## 16. Logging & Observability
Structured CloudWatch logs with correlation/execution ID. Log campaign_id, day, execution_id, stage, status, duration, error_category. Never log auth data. Metrics: ArticlesGenerated, ArticlesPublished, GenerationFailures, PublicationFailures, AuthenticationFailures, DuplicateRunsPrevented, CampaignsCompleted. Minimum useful alarms.

## 17. Error Handling
Categorize: Generation (Bedrock unavailable, malformed, validation), Publishing (auth expired, unavailable, timeout, rejected, unknown result), Infrastructure (DynamoDB, SNS, Lambda timeout). Bounded retries with backoff. Never blindly retry auth failures. Never retry an ambiguous publication result without checking whether it actually published.

## 18. Cost
Target < USD 5 for the 21-day campaign. Provide a cost breakdown (Lambda, EventBridge, DynamoDB, Bedrock, SNS, CloudWatch, API Gateway, browser automation). Identify the largest cost uncertainty.

## 19. Infrastructure as Code
Prefer Terraform unless repo establishes otherwise. Reproducible. Separate app code / infra config / secrets. Never commit secrets. Provide plan/apply commands.

## 20. Testing
Unit: state transitions, validation, Bedrock parsing, idempotency, scheduling, retry, notification payloads. Integration: DynamoDB, Bedrock, SNS, orchestrator. Publishing: adapter test mode with MockPublisher and real BuilderCenterPublisher. No real publishing in automated tests unless requested.

## 21. Security Testing
Check overly permissive IAM, secrets in source/logs, unauthenticated APIs, insecure env vars, missing encryption, injection, unsafe model output, SSRF, replay, duplicate execution, unauthorized campaign modification. Least privilege.

## 22. Development Strategy (Phased)
Phase 0 Discovery; Phase 1 Foundation; Phase 2 Campaign Engine; Phase 3 Bedrock; Phase 4 SNS; Phase 5 Builder Publishing Discovery (MockPublisher first, BuilderCenterPublisher only after validation); Phase 6 Scheduler & API; Phase 7 End-to-End. Work in small verified increments.

## 23-27. DX, Config, Quality, Architecture, Future-Proofing
Clear README with runnable commands. No hard-coded campaign ID/email/phone/schedule/timezone/model/region/topic/publishing config; example config with placeholders only. Clean modules, small functions, strong typing, dependency injection, meaningful error types, structured logging, testable interfaces. Orchestrator coordinates; services own logic. Replaceable publisher and Bedrock implementations. Completion by `day >= campaign.total_days`, never `if day == 21`. No hard-coded topics.

## 28. Definition of Done
Deploy via IaC; create campaign; propose + approve sequence; configure schedule; manual RUN; Bedrock generate; validate; publish via adapter; record result; SNS notify; query status; retry failed day; prevent duplicates; pause/resume; auto-advance; complete 21 days; create another campaign without code changes.

## 29. Critical Rule
Do not mark a feature complete just because it compiles. Each phase: implement, test, lint/type-check, deploy where appropriate, real verification, inspect logs, fix, document. End each phase with: IMPLEMENTED / TESTED / VERIFIED / KNOWN ISSUES / NEXT STEP.

## 30. First Action
Read prd.md, inspect repo, identify structure, produce architecture + task-broken implementation plan, identify risks (esp. Builder Center auth/publishing), identify contradictions, minimum AWS resources, 21-day cost estimate, recommended services. Then begin Phase 1. Keep PRD as source of truth; update docs when a decision changes an assumption. Build a production-quality MVP.
