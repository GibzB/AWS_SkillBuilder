# Product Requirements Document (PRD)
## AWS Builder 21-Day Learning Automation Engine

**Version:** 1.0  
**Status:** Draft for Kiro implementation  
**Owner:** Billy Gibendi  
**Primary timezone:** EAT (UTC+3)  
**Target campaign:** 21 days  
**Target AWS budget:** < USD 5 for the automation itself

---

## 1. Product Overview

Build a low-cost, serverless automation system that manages a reusable series of AWS Builder Center articles based on the user's AWS learning journey.

The user will define and approve the learning themes once. The system will then:

1. maintain the campaign schedule;
2. generate a simple AWS "hack" for each day;
3. use Amazon Bedrock to transform the approved topic into an authentic first-person Builder Center article;
4. validate the generated content against Builder Center article requirements;
5. publish the article through an authenticated publishing adapter;
6. notify the user of success or failure through Amazon SNS;
7. retain campaign and publication history so the same engine can later run another 21-, 30-, or custom-day series.

The MVP should prioritize low cost, simplicity, security, observability, and reliable execution over feature breadth.

AWS Builder Center documentation confirms that signed-in users can create and publish articles, with fields including title, description, body and up to five tags. Published content undergoes automated moderation before appearing on the site.

---

## 2. Problem Statement

Today, creating a daily AWS Builder Center article requires the user to:

- decide what AWS topic to write about;
- formulate a useful beginner-friendly hack;
- write and format the article;
- log into Builder Center;
- create the article;
- publish it;
- remember whether it was successfully published.

This creates unnecessary cognitive and operational overhead and makes maintaining a 21-day learning series difficult.

The system should remove repetitive work while preserving the user's voice and control over the learning journey.

---

## 3. Product Vision

Create a reusable "learning-to-content" automation engine:

> Define what I want to learn and share → let the system turn it into a useful AWS learning article → publish it to Builder Center → tell me what happened.

The product should feel like a personal AWS content operations assistant rather than a generic AI content generator.

---

## 4. Goals

### Primary goals

- Automate a 21-day AWS Builder Center article campaign.
- Base the series on practical AWS fundamentals learned over the user's past year of AWS use.
- Keep individual hacks simple enough for beginners/intermediate builders.
- Generate articles in first person and in the user's authentic learning voice.
- Use Amazon Bedrock for light AI generation rather than an autonomous agent.
- Use AWS-native serverless components wherever practical.
- Allow the user to manually trigger a run.
- Support scheduled daily execution through Amazon EventBridge.
- Notify the user through Amazon SNS.
- Preserve article/campaign state and publication history.
- Keep automation infrastructure costs below USD 5 for the initial campaign, excluding external services or AWS credits consumed by unrelated workloads.
- Make the engine reusable for future campaigns.

### Secondary goals

- Allow configurable campaign length.
- Allow configurable execution time.
- Allow pause/resume.
- Allow retry of failed publication.
- Provide status information.
- Prevent duplicate publication.

---

## 5. Non-Goals

The MVP will NOT:

- provision AWS infrastructure for the daily hacks;
- execute commands against the user's AWS workload;
- create paid AWS resources for demonstration purposes;
- automatically browse AWS documentation to invent complex architectures;
- operate as a fully autonomous AI agent;
- generate cover images;
- require a frontend application;
- store the user's Builder ID password;
- automate MFA or attempt to bypass authentication/security controls;
- publish content without passing validation;
- create content unrelated to the approved campaign topics.

---

## 6. Target User

### Primary user

The system is designed for a single AWS builder who wants to document and share practical AWS learning through a daily Builder Center article series.

### MVP tenancy

Single-user.

The architecture should avoid unnecessary multi-tenant complexity but should keep campaign data sufficiently structured to support future expansion.

---

# 7. User Journey

## Initial setup

1. User deploys the application.
2. User configures notification destinations.
3. User establishes the supported Builder Center authentication/session mechanism.
4. User defines a campaign.
5. System proposes or assembles the 21 AWS topics.
6. User reviews/approves the campaign.
7. Campaign becomes ready for execution.

## Daily execution

1. EventBridge triggers the orchestration Lambda at the configured time.
2. Lambda determines the next eligible campaign day.
3. System retrieves that day's approved topic.
4. Bedrock generates the article.
5. Content validator checks the article.
6. Publishing adapter authenticates using the configured secure session mechanism.
7. Article is submitted to Builder Center.
8. System records the result.
9. SNS sends a success/failure notification.
10. Campaign advances to the next day only after successful submission.

## Manual execution

The user can invoke a Lambda Function URL or API Gateway endpoint to execute the same workflow manually.

---

# 8. Content Strategy

The initial 21-day series should cover AWS fundamentals and practical lessons.

Proposed sequence:

1. AWS account fundamentals and security mindset
2. IAM users, roles and least privilege
3. Amazon S3 fundamentals
4. S3 lifecycle and storage optimization
5. Amazon EC2 fundamentals
6. EC2 security groups
7. Amazon VPC fundamentals
8. Subnets, route tables and internet access
9. AWS Lambda
10. Amazon API Gateway
11. Amazon EventBridge
12. Amazon CloudWatch
13. AWS Systems Manager
14. Amazon DynamoDB
15. Amazon RDS fundamentals
16. Amazon SNS
17. Infrastructure as Code with Terraform/CloudFormation
18. AWS security fundamentals
19. AWS cost optimization
20. Serverless architecture patterns
21. Lessons learned: connecting the AWS fundamentals

The exact order may be changed during campaign creation.

### Content principle

The series should tell a coherent learning story:

> "These are practical things I learned while working with AWS, explained simply enough that another builder can reproduce the idea safely."

---

# 9. Article Requirements

Every article must contain:

### Required metadata

- Unique title
- Description
- Markdown body
- 1–5 relevant tags

### Body structure

Recommended structure:

```text
# Introduction

What I was learning and why it mattered.

## The AWS concept

Explain the concept simply.

## The simple hack

Show the practical technique.

## How it works

Explain the moving parts.

## Example

Provide a safe example using commands, configuration, architecture notation, or pseudocode where useful.

## What I learned

Explain the practical lesson in first person.

## Things to watch out for

Mention security, cost, permissions, or operational considerations.

## Takeaway

A concise conclusion.
```

Articles should not claim that the user executed an AWS operation if the automation did not actually execute it.

---

# 10. Markdown Diagrams

Images are explicitly out of MVP scope.

Markdown-based diagrams MAY be included where useful.

Because Builder Center rendering behavior for advanced diagram syntaxes may vary, the MVP should prefer portable representations such as fenced text/ASCII diagrams.

Example:

```text
User
  |
  v
API Gateway / Lambda URL
  |
  v
Orchestrator Lambda
  |
  +----> DynamoDB
  |
  +----> Bedrock
  |
  +----> Builder Publishing Adapter
  |
  v
SNS notification
```

Mermaid syntax may be supported later, but the generated article should not depend on Mermaid rendering for comprehension.

---

# 11. AI Requirements

## AI role

Amazon Bedrock is a content-generation component, not an autonomous agent.

Bedrock should:

- turn an approved topic into an article;
- preserve first-person perspective;
- use beginner-friendly explanations;
- create practical examples;
- avoid inventing personal experiences;
- avoid claiming infrastructure was deployed when it was not;
- avoid fabricated measurements, costs, results, or screenshots;
- produce valid Builder Center metadata;
- remain within configured length limits.

## AI should NOT

- independently select unrelated topics;
- publish directly;
- modify the approved campaign scope;
- invent certifications or professional experiences;
- fabricate AWS execution results;
- make security-sensitive decisions without deterministic validation.

---

# 12. Suggested Bedrock Prompt Contract

The generation layer should receive structured input similar to:

```json
{
  "campaign_id": "aws-learning-21",
  "day": 1,
  "topic": "IAM least privilege",
  "difficulty": "beginner",
  "voice": "first_person",
  "context": "AWS learning journey over the past year",
  "executed_in_aws": false,
  "article_requirements": {
    "title_max_chars": 255,
    "description_max_chars": 512,
    "max_tags": 5
  }
}
```

The model should return structured JSON:

```json
{
  "title": "...",
  "description": "...",
  "body_markdown": "...",
  "tags": ["aws", "iam", "security"],
  "learning_takeaway": "..."
}
```

The application must validate the output before publication.

---

# 13. Functional Requirements

## FR-001 Campaign creation

The system shall allow creation of a campaign with:

- campaign name;
- description;
- number of days;
- start date;
- execution time;
- timezone;
- topic list;
- content style;
- notification settings.

## FR-002 Topic planning

The system shall generate a proposed sequence of AWS topics based on the campaign objective.

The user shall be able to review and approve the sequence before daily generation begins.

## FR-003 Daily topic selection

The system shall identify exactly one eligible topic for each campaign execution.

## FR-004 Content generation

The system shall generate an article using Amazon Bedrock.

## FR-005 Content validation

The system shall validate:

- title presence;
- title <= 255 characters;
- description presence;
- description <= 512 characters;
- body presence;
- Markdown validity;
- 1–5 tags;
- no obvious placeholder text;
- no fabricated execution claims;
- no duplicate campaign publication;
- article topic matches the approved topic.

## FR-006 Publication

The system shall submit validated content through a Builder Center publishing adapter.

The adapter shall isolate Builder Center-specific authentication and UI/API behavior from the rest of the application.

## FR-007 Authentication

The system shall never store the Builder ID password in source code, Lambda environment variables, logs, DynamoDB, or plaintext configuration.

A supported authentication/session mechanism shall be established manually.

If Builder Center does not expose a supported publishing API, the implementation must use a controlled browser automation adapter only where permitted by the site's terms and technical controls.

The system shall fail safely rather than attempt to bypass authentication, MFA, CAPTCHA, bot detection, or other access controls.

## FR-008 Notification

The system shall publish execution results through Amazon SNS.

Notifications shall include:

- campaign;
- day;
- article title;
- status;
- timestamp;
- Builder Center URL when available;
- error summary when failed.

## FR-009 Manual run

The user shall be able to manually trigger the campaign through a low-cost authenticated Lambda invocation mechanism.

Preferred MVP options:

1. Lambda Function URL with authentication, or
2. API Gateway HTTP API.

## FR-010 Scheduled run

Amazon EventBridge shall trigger the workflow at a configurable time.

## FR-011 Retry

Failed executions shall be retryable without creating duplicate articles.

## FR-012 Status

The system shall expose campaign status including:

- current day;
- completed days;
- failed days;
- next scheduled execution;
- last execution;
- publication URL;
- campaign state.

## FR-013 Pause/resume

The user shall be able to pause and resume a campaign.

## FR-014 Campaign completion

After the final day successfully publishes, the campaign shall enter `COMPLETED`.

The system shall not continue publishing unless a new campaign is created.

---

# 14. Simplified Campaign State Machine

```text
                 +----------+
                 |   DRAFT  |
                 +----+-----+
                      |
                      v
                +-----------+
                |  APPROVED |
                +-----+-----+
                      |
                      v
                +-----------+
                |   READY   |
                +-----+-----+
                      |
                 RUN / SCHEDULE
                      |
                      v
                +-----------+
                | GENERATING|
                +-----+-----+
                      |
                 validation
                 /        \
                v          v
            +-------+   +--------+
            | READY |   | FAILED |
            +---+---+   +---+----+
                |           |
                v           |
           PUBLISHING <-----+
                |
           +----+----+
           |         |
           v         v
       PUBLISHED   FAILED
           |
           v
      NEXT DAY / COMPLETE
```

---

# 15. Proposed AWS Architecture

The MVP should use:

- Amazon EventBridge Scheduler
- AWS Lambda
- Amazon Bedrock
- Amazon DynamoDB
- AWS Secrets Manager
- Amazon SNS
- Amazon CloudWatch
- optional Amazon S3 for generated artifacts/logical content archive
- optional API Gateway HTTP API OR Lambda Function URL

High-level architecture:

```text
                    +----------------------+
                    | EventBridge Scheduler|
                    +----------+-----------+
                               |
                               v
                    +----------------------+
                    | Orchestrator Lambda  |
                    +----------+-----------+
                               |
              +----------------+----------------+
              |                |                |
              v                v                v
        +-----------+    +-----------+    +-------------+
        | DynamoDB  |    |  Bedrock  |    | Secrets Mgr |
        +-----------+    +-----------+    +-------------+
              |                |
              |                v
              |        Generated Article
              |                |
              +-------+--------+
                      |
                      v
             +---------------------+
             | Publishing Adapter  |
             | Builder Center      |
             +----------+----------+
                        |
                        v
                 AWS Builder Center
                        |
                        v
                 +-------------+
                 | SNS Notify  |
                 +-------------+
                  /           \
               Email         SMS
```

---

# 16. Publishing Adapter Design

The publishing mechanism is the highest-risk technical component.

The application must abstract it behind an interface:

```text
publish_article(article) -> PublicationResult
```

Possible implementation strategies shall be evaluated in this order:

1. Official/supported Builder Center publishing API, if available.
2. Supported authenticated integration mechanism.
3. Controlled browser automation using a short-lived/persisted authenticated session, only if permitted by Builder Center terms and technically reliable.

The rest of the application must not depend on browser-specific implementation details.

Potential browser automation may require a Lambda container image with a headless Chromium/Playwright runtime. This should NOT be selected automatically; Kiro must first validate whether the target publishing flow can operate reliably in Lambda.

Authentication artifacts must be encrypted at rest and protected by least-privilege IAM.

The system must never attempt to circumvent CAPTCHA, MFA, bot protection, rate limits, or other security controls.

---

# 17. Security Requirements

## Credentials

- Never store Builder ID password.
- Never log authentication cookies/tokens.
- Never commit secrets to Git.
- Use AWS Secrets Manager for supported sensitive session material.
- Encrypt secrets at rest.
- Restrict Lambda IAM permissions.

## AWS IAM

Use separate least-privilege permissions for:

- DynamoDB access;
- Bedrock invocation;
- Secrets Manager read;
- SNS publish;
- CloudWatch logging.

## API

If Lambda Function URL/API Gateway is exposed publicly:

- require authentication;
- do not expose an unauthenticated RUN endpoint;
- implement replay/duplicate protection;
- validate request payloads.

## Logging

Logs must exclude:

- passwords;
- cookies;
- access tokens;
- session headers;
- personal authentication details.

---

# 18. Data Model

## Campaign

```json
{
  "campaign_id": "string",
  "name": "string",
  "description": "string",
  "status": "DRAFT|APPROVED|RUNNING|PAUSED|COMPLETED",
  "total_days": 21,
  "timezone": "Africa/Nairobi",
  "start_date": "YYYY-MM-DD",
  "execution_time": "HH:MM",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

## Campaign Day

```json
{
  "campaign_id": "string",
  "day": 1,
  "topic": "string",
  "status": "PLANNED|GENERATED|READY|PUBLISHED|FAILED|SKIPPED",
  "article_id": "string",
  "title": "string",
  "description": "string",
  "body_markdown": "string",
  "tags": [],
  "publication_url": "string",
  "attempt_count": 0,
  "last_error": "string",
  "published_at": "ISO-8601"
}
```

---

# 19. Idempotency

The system MUST be idempotent.

A repeated EventBridge invocation or manual RUN must not publish the same campaign day twice.

Before publishing, the system must check:

```text
campaign_id + day
```

and confirm that the day is not already marked `PUBLISHED`.

A publication attempt should also receive a unique idempotency key.

---

# 20. Error Handling

Failures must be categorized:

### Generation failure

Examples:

- Bedrock unavailable;
- malformed model response;
- validation failure.

Action:

- mark day `FAILED`;
- do not publish;
- notify user.

### Authentication failure

Action:

- mark day `FAILED`;
- notify user;
- provide remediation instructions;
- do not repeatedly retry indefinitely.

### Builder publishing failure

Action:

- retry according to bounded retry policy;
- avoid duplicate publication;
- notify after final failure.

### Notification failure

Notification failure must NOT cause the article publication itself to be considered failed.

---

# 21. Observability

Use Amazon CloudWatch for:

- structured application logs;
- execution duration;
- generation success/failure;
- publication success/failure;
- retry counts;
- authentication failures;
- campaign completion.

Useful metrics:

```text
ArticlesGenerated
ArticlesPublished
PublicationFailures
GenerationFailures
AuthenticationFailures
CampaignsCompleted
DuplicateRunsPrevented
```

---

# 22. Cost Requirements

Target: **under USD 5 for the 21-day MVP**.

The design should minimize:

- Lambda duration;
- DynamoDB usage;
- API Gateway usage;
- Bedrock token consumption;
- CloudWatch log volume;
- browser automation runtime.

Prefer:

- DynamoDB on-demand;
- Lambda;
- EventBridge Scheduler;
- SNS;
- minimal CloudWatch retention;
- small Bedrock models suitable for the task.

Browser automation may dominate the cost/complexity profile if required.

The implementation must include a cost estimate before deployment.

---

# 23. Scheduling

The user shall configure:

- start date;
- time;
- timezone;
- campaign duration.

Default timezone:

```text
Africa/Nairobi
UTC+03:00
```

The system must use timezone-aware scheduling.

EventBridge should be responsible for triggering execution rather than keeping a Lambda running continuously.

---

# 24. Notification Requirements

Amazon SNS shall provide the notification abstraction.

At minimum:

### Success

```text
AWS Builder Automation — Day 7 Published

Campaign: My AWS Learning Journey
Article: <title>
Status: PUBLISHED
URL: <Builder Center URL>
Time: <timestamp>
```

### Failure

```text
AWS Builder Automation — Day 7 Failed

Campaign: My AWS Learning Journey
Stage: Builder publishing
Status: FAILED
Reason: <sanitized error>
Action: Retry after resolving the issue.
```

Email is the primary notification channel.

SMS may be enabled through SNS if required and configured.

---

# 25. API / Command Interface

The MVP should expose a minimal command interface.

Preferred operations:

```text
POST /campaign
POST /campaign/{id}/approve
POST /campaign/{id}/run
POST /campaign/{id}/retry
GET  /campaign/{id}/status
POST /campaign/{id}/pause
POST /campaign/{id}/resume
```

For the smallest MVP, `RUN` and `STATUS` may be implemented first.

---

# 26. Content Quality Gates

An article MUST NOT be published if:

- title is missing;
- description is missing;
- body is empty;
- tags exceed five;
- title exceeds 255 characters;
- description exceeds 512 characters;
- placeholders remain;
- generated content contains obvious hallucinated personal experience;
- article claims resources were deployed when they were not;
- topic does not match the campaign day;
- duplicate publication is detected.

---

# 27. Authenticity Rules

The generated content should sound like the user's AWS learning journey.

Preferred:

> "One thing I learned while working with AWS is that IAM becomes much easier to reason about when I separate who is making the request from what that identity is allowed to do."

Avoid:

> "As an AWS expert with decades of experience..."

The system must never invent:

- employment history;
- projects;
- production incidents;
- certifications;
- customer environments;
- performance measurements;
- personal experiences.

---

# 28. Content Difficulty

The initial series should target:

**Beginner → Lower Intermediate**

Each hack should be:

- simple;
- reproducible;
- safe;
- inexpensive;
- educational;
- based on a fundamental AWS concept.

Each article should explain not just "what to do" but "why it works."

---

# 29. Acceptance Criteria

## Campaign

- [ ] User can create a campaign.
- [ ] System can propose 21 topics.
- [ ] User can approve the topic sequence.
- [ ] Campaign persists in DynamoDB.

## Generation

- [ ] System generates one article for the current day.
- [ ] Article uses first-person voice.
- [ ] Article follows required metadata limits.
- [ ] Article contains valid Markdown.
- [ ] Article passes deterministic validation.

## Publishing

- [ ] Publishing adapter can authenticate using the configured secure mechanism.
- [ ] Article can be submitted to Builder Center.
- [ ] Published URL is captured when available.
- [ ] Duplicate publication is prevented.
- [ ] Authentication failures fail safely.

## Scheduling

- [ ] EventBridge triggers daily execution.
- [ ] User can manually trigger RUN.
- [ ] Timezone is configurable.
- [ ] Campaign stops after final successful day.

## Notifications

- [ ] Success notification is sent.
- [ ] Failure notification is sent.
- [ ] Notification failures do not corrupt campaign state.

## Operations

- [ ] User can view campaign status.
- [ ] User can retry failed days.
- [ ] User can pause/resume.
- [ ] CloudWatch logs show execution outcomes.
- [ ] Secrets never appear in logs.

## Cost

- [ ] Architecture has an estimated MVP cost below USD 5 for 21 days.
- [ ] No unnecessary always-on infrastructure is deployed.

---

# 30. Future Enhancements

Potential post-MVP features:

- 30/60/90-day campaigns;
- multiple AWS learning tracks;
- automated topic research;
- AWS documentation citation retrieval;
- article performance analytics;
- automatic series indexing;
- dashboard;
- content versioning;
- human approval workflow;
- richer architecture diagrams;
- content repurposing to LinkedIn/X;
- multi-user support;
- multiple publishing destinations.

These are explicitly outside MVP.

---

# 31. Kiro Implementation Principles

Kiro should implement the system incrementally.

### Phase 1 — Foundation

- project structure;
- infrastructure as code;
- DynamoDB;
- Lambda;
- configuration;
- CloudWatch.

### Phase 2 — Campaign engine

- campaign creation;
- topic storage;
- state management;
- idempotency.

### Phase 3 — Bedrock

- prompt;
- structured output;
- content validation;
- retry handling.

### Phase 4 — Notifications

- SNS;
- email;
- optional SMS;
- success/failure messages.

### Phase 5 — Builder publishing adapter

- investigate supported publishing mechanism;
- implement adapter;
- implement authentication/session management;
- test against Builder Center;
- ensure failure-safe behavior.

### Phase 6 — Scheduling/API

- EventBridge Scheduler;
- authenticated manual RUN endpoint;
- status endpoint;
- retry/pause/resume.

### Phase 7 — End-to-end validation

Run a complete test campaign using a non-production/test-safe publishing path where possible.

Verify:

```text
Schedule
  -> Lambda
  -> DynamoDB
  -> Bedrock
  -> Validation
  -> Builder Center
  -> Publication
  -> DynamoDB update
  -> SNS
```

---

# 32. Definition of Done

The MVP is complete when the user can:

1. create a campaign;
2. approve a 21-day AWS fundamentals learning sequence;
3. configure a daily schedule;
4. trigger a run manually;
5. have Bedrock generate that day's first-person article;
6. pass deterministic validation;
7. publish the article to AWS Builder Center through the implemented permitted publishing mechanism;
8. receive an SNS notification;
9. see the publication result;
10. retry failures without duplicate publication;
11. pause/resume the campaign;
12. complete all 21 days;
13. create another campaign using the same engine.

The system must remain secure, observable, inexpensive, and reusable.

---

## 33. Important Technical Decision

The Builder Center publishing mechanism MUST be treated as a discovery/validation task before the rest of the implementation is locked.

AWS Builder Center officially documents article creation as a signed-in workflow: create article, populate fields, preview, publish, and wait for automated moderation. Builder ID is the credential used for publishing. Therefore, Kiro must not assume that a public API exists.

The implementation should first determine whether an official/supported programmatic publishing interface is available. If not, the publishing adapter may use controlled browser automation only after confirming that doing so is compatible with the site's terms and does not require bypassing security controls.

This decision should be isolated so the rest of the serverless application remains unchanged if the publishing mechanism must later be replaced.

---

## 34. Initial 21-Day Campaign

The first campaign should be titled something similar to:

**21 Days of AWS: What I Learned Building with the Cloud**

The tone should be:

- personal;
- practical;
- honest;
- beginner-friendly;
- technically accurate;
- concise;
- experience-driven.

The series should document learning rather than present the author as an unquestionable authority.

