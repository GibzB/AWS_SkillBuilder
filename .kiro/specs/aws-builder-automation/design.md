# Design Document

## AWS Builder Learning Automation Engine

## Overview

The AWS Builder Learning Automation Engine is a low-cost, AWS-native, serverless system that turns an approved AWS learning topic into a first-person Builder Center article every campaign day, validates it deterministically, publishes it through an isolated publishing adapter, records the outcome, and notifies the user through Amazon SNS. The engine is reusable for any campaign length between 1 and 365 days, with completion determined by comparing the published day number against the campaign total day count (never a hard-coded value).

This design follows AWS Well-Architected principles, favoring event-triggered serverless components that idle at zero cost between executions. There is no always-on compute. A single Orchestrator Lambda coordinates each campaign-day execution but delegates business logic to small, strongly typed, dependency-injected modules (Campaign_Manager, Topic_Planner, Content_Generator, Content_Validator, Publishing_Adapter, Notifier).

The single most important architectural decision is the treatment of Builder Center publishing. Web research (AWS sources, February to March 2026) confirms there is no documented public or official AWS Builder Center article-publishing API. Builder Center is a signed-in web experience, and AWS Builder ID now supports federated sign-in (GitHub, Amazon, Apple, Google) and MFA, which raises the risk and fragility of any automation. Content was rephrased for compliance with licensing restrictions. Because of this, the real `BuilderCenterPublisher` is treated as a discovery and validation task that is NOT assumed to work, and the deployable MVP defaults to the `MockPublisher`. All publishing is isolated behind the `Publishing_Adapter` interface (`publish_article(article) -> PublicationResult`) so the rest of the system is unaffected if the real publisher is later implemented, replaced, or abandoned.

### Requirements Coverage Map

| Design Section | Requirements Addressed |
| --- | --- |
| Architecture | 8.7, 11, 18.1, 18.3, 19.1 |
| Technology and Language Choice | 4, 6, 18 |
| Components and Interfaces | 1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14 |
| Data Models | 1, 3, 5, 7, 9, 12, 13, 15 |
| State Machines | 2, 14, 15, 16 |
| Idempotency Design | 9, 12 |
| Bedrock Design | 4, 12, 20 |
| Content Validation Design | 5, 20 |
| API Design | 9, 11, 13, 14 |
| Scheduling | 11, 23 |
| Notifications | 8, 10 |
| Security and IAM | 8, 17 |
| Observability | 17 |
| Error Handling | 4, 7, 8, 12 |
| Cost Estimate | 18 |
| Infrastructure as Code | 18, 19 |
| Correctness Properties | 5, 9, 15, 16, 17 |
| Testing Strategy | all |

## Architecture

The system is event-triggered and serverless. EventBridge Scheduler (or an authenticated Command_API request) invokes the Orchestrator Lambda, which coordinates the campaign store (DynamoDB), the content service (Amazon Bedrock), and the configured Publishing_Adapter, then emits an outcome through Amazon SNS. Secrets Manager holds session material resolved only at runtime. CloudWatch captures structured logs and metrics.

```text
                          +-----------------------------+
                          |   EventBridge Scheduler     |
                          | (timezone-aware, per day)   |
                          +--------------+--------------+
                                         |
              authenticated              | scheduled trigger
              manual command             v
     +--------------------+     +-----------------------------+
     |    Command_API     |---->|     Orchestrator Lambda     |
     | (Lambda Fn URL,    |     | coordinates only, delegates |
     |  IAM auth)         |     | business logic to modules   |
     +--------------------+     +--+--------+---------+-------+
                                   |        |         |
                    +--------------+        |         +----------------+
                    |                       |                          |
                    v                       v                          v
          +-------------------+   +--------------------+   +------------------------+
          | Campaign Store    |   |  Content Service   |   |  Publishing Adapter    |
          | Amazon DynamoDB   |   |  Amazon Bedrock    |   |  (selected by config)  |
          | on-demand         |   |  small model       |   |  MockPublisher (MVP)   |
          | conditional writes|   |  JSON output       |   |  BuilderCenterPublisher|
          +-------------------+   +--------------------+   +-----------+------------+
                    ^                                                  |
                    |                                    +-------------+-------------+
                    |                                    |   Secrets Manager         |
                    |                                    | (runtime resolve session) |
                    |                                    +---------------------------+
                    |
                    |  state + outcome recorded
                    v
          +-------------------+                    +-------------------+
          | Amazon SNS Topic  |------------------->|  Email (primary)  |
          | notifications     |                    |  SMS (optional)   |
          +-------------------+                    +-------------------+

          All components emit structured logs and metrics to Amazon CloudWatch,
          correlated by Correlation_ID, with sensitive values redacted.
```

### Daily Execution Sequence

```text
Scheduler   Orchestrator   DynamoDB    Bedrock   Validator  Publisher   Notifier   SNS
   |             |             |          |          |          |          |         |
   |--trigger--->|             |          |          |          |          |         |
   |             |--select lowest eligible day------>|          |          |         |
   |             |<---day (PLANNED/FAILED)-|          |          |          |         |
   |             |--set GENERATING-------->|          |          |          |         |
   |             |--generate(spec)----------------->|            |          |         |
   |             |<--Article (or GenerationError)---|            |          |         |
   |             |--set GENERATED--------->|          |          |          |         |
   |             |--validate(article, approved_topic, state)---->|          |         |
   |             |<--ValidationResult------------------|         |          |         |
   |             |  (invalid) set FAILED, notify failure ------------------>|-------->|
   |             |--set READY------------->|          |          |          |         |
   |             |--set PUBLISHING-------->|          |          |          |         |
   |             |--publish_article(article)------------------->|          |         |
   |             |<--PublicationResult--------------------------|          |         |
   |             |  (ambiguous) verify_published before re-publish          |         |
   |             |--conditional PUBLISHED write (day <> PUBLISHED)-->|      |         |
   |             |<--ok / already-published (prevented duplicate)---|      |         |
   |             |--record url + published_at, advance / complete-->|      |         |
   |             |--notify success------------------------------------------>|------>|
   |             |  (all logs/metrics carry Correlation_ID, secrets redacted)|       |
```

Design intent per Well-Architected principles:

- Cost optimization: nothing runs between executions. DynamoDB is on-demand, Lambda is invoked only on schedule or command, and Bedrock uses a small model (Requirements 18.1, 18.2, 18.3).
- Security: least-privilege IAM scoped per resource, secrets resolved at runtime, no password storage, no control bypass (Requirement 8).
- Reliability: idempotent conditional writes, bounded retries, ambiguous-result verification (Requirements 9, 12).
- Operational excellence: structured logs, named metrics, correlation IDs (Requirement 17).

## Technology and Language Choice

Recommendation: Python 3.12 on AWS Lambda for the application (Orchestrator and modules), with Terraform for infrastructure.

Evaluation of Python vs TypeScript for this workload:

| Factor | Python 3.12 | TypeScript (Node 20) |
| --- | --- | --- |
| Bedrock SDK ergonomics | boto3 `bedrock-runtime` is mature and idiomatic | aws-sdk v3 works well too |
| Markdown validation | strong ecosystem (`markdown-it-py`, `commonmark`) | comparable (`markdown-it`) |
| Property-based testing | Hypothesis is best-in-class | fast-check is good but less mature for stateful models |
| Playwright option (browser automation candidate) | Playwright Python supported | Playwright Node is the reference implementation |
| Cold start / package size | acceptable; smaller without browser | slightly faster cold start |
| Type safety | type hints + `mypy` (opt-in) | native, stronger by default |

Decision rationale: both are viable. Python is recommended because (1) Hypothesis gives the strongest property-based testing story for the correctness properties in this design, (2) boto3 Bedrock and DynamoDB conditional-write ergonomics are excellent, and (3) if the discovery task later selects browser automation, Playwright Python runs cleanly in a Lambda container image. Strong typing is enforced with type hints plus `mypy` in CI. The Publishing_Adapter interface keeps the browser-automation dependency out of the default deployment entirely, so the base Lambda stays small.

Engineering conventions applied throughout:

- Dependency injection: the Orchestrator receives its collaborators (store, generator, validator, publisher, notifier, clock) as constructor arguments, so tests inject fakes and configuration selects the publisher.
- Small modules with single responsibility, each behind an explicit interface.
- Meaningful error types: `ValidationError`, `GenerationError`, `AuthenticationError`, `PublicationError`, `AmbiguousPublicationError`, `IdempotencyConflict`, `ConfigurationError`, `StateTransitionError`. These drive the error-handling policy.
- Structured JSON logging with a `Correlation_ID` and automatic redaction of sensitive keys.

## Components and Interfaces

All components are pure-logic modules where possible, with I/O pushed to injected boundaries (DynamoDB client, Bedrock client, SNS client, Secrets Manager client, clock). This keeps the core logic property-testable.

### Shared Contracts

```python
# Article: the structured content produced by generation and consumed by publishing.
@dataclass(frozen=True)
class Article:
    campaign_id: str
    day: int
    topic: str
    title: str            # 1..255 chars after trim
    description: str      # 1..512 chars after trim
    body_markdown: str    # >= 1 char after trim, valid Markdown
    tags: tuple[str, ...] # 1..5 tags
    learning_takeaway: str
    executed_in_aws: bool # anti-fabrication flag; MVP default False

# PublicationResult: the sole output contract of every Publishing_Adapter.
@dataclass(frozen=True)
class PublicationResult:
    status: Literal["SUCCESS", "FAILURE"]
    publication_url: str          # 1..2048 chars on success-with-url, "" when unavailable
    error_summary: str            # "" on success; sanitized, no auth material on failure
    ambiguous: bool = False       # True when success/failure could not be determined

# ValidationResult: structured validation outcome.
@dataclass(frozen=True)
class ValidationViolation:
    code: str    # e.g. "TITLE_TOO_LONG", "TOPIC_MISMATCH", "DUPLICATE_PUBLICATION"
    field: str   # e.g. "title", "tags", "body_markdown"
    message: str # human-readable, safe to log and notify

@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    violations: tuple[ValidationViolation, ...]  # empty iff is_valid
```

### Campaign_Manager

Owns campaign lifecycle and Campaign_State transitions (Requirements 1, 2.4 to 2.8, 14, 15, 16.1, 16.2).

```python
class CampaignManager:
    def create_campaign(self, request: CreateCampaignRequest) -> Campaign: ...   # Req 1
    def submit_topics(self, campaign_id: str, topics: list[str]) -> Campaign: ... # Req 2.6, 2.7
    def approve(self, campaign_id: str) -> Campaign: ...    # Req 2.4, 2.5, 2.8 (APPROVED then READY)
    def pause(self, campaign_id: str) -> Campaign: ...      # Req 14.1, 14.4
    def resume(self, campaign_id: str) -> Campaign: ...     # Req 14.3, 14.5
    def transition(self, campaign_id: str, target: CampaignState) -> Campaign: ...# Req 16.1, 16.2
    def complete_if_final(self, campaign_id: str, published_day: int) -> Campaign: ...# Req 15.1, 15.3
```

Create-campaign validation enforces name 1..200, description 0..1000, topic list 1..365 entries, total day count 1..365, ISO-8601 start date, HH:MM execution time, and defaults timezone to Africa/Nairobi when omitted (Requirements 1.1 to 1.6, 1.7, 1.8, 15.5). Rejections return a validation error naming every offending field.

### Topic_Planner

Produces a proposed ordered topic sequence whose length equals the campaign total day count, one non-empty unique topic per day (Requirements 2.1, 2.2, 2.3). The planner draws from a curated AWS fundamentals catalog (the PRD 21-topic sequence as a seed) and generalizes for any length by composing fundamental themes; it never uses hard-coded topic values tied to a fixed day count (Requirement 15.4). If the total day count is out of range 1..365 it rejects and names the permitted range.

```python
class TopicPlanner:
    def propose(self, total_days: int, objective: str) -> list[str]: ...  # len == total_days, unique, non-empty
```

### Content_Generator

The Bedrock generation layer (Requirements 4, 20.1, 20.2). It builds the structured request, invokes Bedrock with a 120-second timeout, parses JSON output into an `Article`, and classifies generation failures. It never publishes.

```python
class ContentGenerator:
    def generate(self, spec: GenerationSpec) -> Article: ...  # raises GenerationError on failure/timeout/parse
```

### Content_Validator

Deterministic gate applied before publication (Requirements 5, 20.3, 20.4). Returns a `ValidationResult` with a violation per failed check. Detailed checks appear in the Content Validation Design section.

```python
class ContentValidator:
    def validate(self, article: Article, approved_topic: str,
                 existing_day_state: DayState | None) -> ValidationResult: ...
```

### Publishing_Adapter (MockPublisher and BuilderCenterPublisher)

The single publish operation isolates all Builder Center specifics (Requirement 6). The Orchestrator references only `publish_article` and `PublicationResult`, never implementation-specific fields.

```python
class PublishingAdapter(Protocol):
    def publish_article(self, article: Article) -> PublicationResult: ...

class MockPublisher:      # Req 6.2 - no network to Builder Center; MVP default
    def publish_article(self, article: Article) -> PublicationResult: ...

class BuilderCenterPublisher:  # Req 6.3 - discovery/validation task, not assumed to work
    def publish_article(self, article: Article) -> PublicationResult: ...
    def verify_published(self, article: Article) -> bool: ...  # Req 12.3, 12.4 ambiguous-result check
```

Selection is by configuration. Exactly one implementation must be selected; zero or more than one is a configuration error that blocks any publish attempt (Requirements 6.4, 6.5). See the Publishing Adapter Strategy section for the discovery decision and fail-safe rules.

### Notifier

Publishes success and failure notifications through SNS with sensitive-value exclusion and notification-failure isolation (Requirement 10).

```python
class Notifier:
    def notify_success(self, ctx: NotificationContext) -> None: ...  # Req 10.1, 10.2
    def notify_failure(self, ctx: NotificationContext) -> None: ...  # Req 10.3, 10.4
```

### Command_API

Authenticated request interface (Requirements 11, 13, 14). Validates payloads and authentication before invoking any downstream component; there is no unauthenticated RUN.

```text
POST /campaign                 create campaign            (Req 1)
POST /campaign/{id}/approve    approve topics             (Req 2.4)
POST /campaign/{id}/run        manual run next day        (Req 11.4, 11.5)
POST /campaign/{id}/retry      retry a FAILED day         (Req 12.1, 16.5)
GET  /campaign/{id}/status     campaign status            (Req 13)
POST /campaign/{id}/pause      pause campaign             (Req 14.1)
POST /campaign/{id}/resume     resume campaign            (Req 14.3)
```

### Orchestrator

Coordinates a single campaign-day execution and delegates business logic. It selects the eligible day, drives Day_State transitions, invokes generation, validation, and publishing, records outcomes idempotently, advances the campaign, and triggers notifications (Requirements 3, 4.1, 4.8, 4.9, 5.13, 5.14, 7, 9, 12, 15.1, 15.2, 16.3 to 16.6). It contains coordination logic only; all rules live in the delegated modules.

## Data Models

Single DynamoDB table `aws-builder-automation` (on-demand, Requirement 18.2), single-table design for campaigns and days.

### Keys and Items

| Item | PK | SK | Purpose |
| --- | --- | --- | --- |
| Campaign | `CAMPAIGN#<campaign_id>` | `META` | Campaign config and Campaign_State |
| Campaign_Day | `CAMPAIGN#<campaign_id>` | `DAY#<zero-padded day>` | Per-day state, article, publication |

Zero-padded day (for example `DAY#0007`) keeps day items sorted numerically under a campaign, so "lowest eligible day" queries are ordered scans of the partition (Requirement 3.1, 3.2).

Campaign item attributes:

```text
pk, sk, campaign_id, name, description, campaign_state,
total_days, timezone, start_date, execution_time,
content_style, notification_email, notification_phone (optional),
publisher_selection, created_at, updated_at
```

Campaign_Day item attributes:

```text
pk, sk, campaign_id, day, topic, day_state,
article_id, title, description, body_markdown, tags,
publication_url, published_at, attempt_count,
last_error, idempotency_key, correlation_id, updated_at,
ttl (optional)
```

### Global Secondary Indexes

- `GSI1` (state index): PK `campaign_id`, SK `day_state`. Access pattern: count completed days and failed days for status, and find eligible (PLANNED or FAILED) days (Requirements 3.1, 13.1).

Because a single campaign has at most 365 day items, a partition Query with a filter is also sufficient and cheaper; `GSI1` is optional and used only if status queries dominate. The MVP uses a partition Query on `CAMPAIGN#<id>` and filters/sorts in memory to minimize cost.

### Access Patterns

| Pattern | Operation | Requirement |
| --- | --- | --- |
| Load campaign | GetItem PK+`META` | 13, 15.3 |
| Select next eligible day | Query PK begins_with `DAY#`, pick lowest day where state in {PLANNED, FAILED}, FAILED first | 3.1, 3.2 |
| Record GENERATING/GENERATED/READY/PUBLISHING/FAILED | UpdateItem with `ConditionExpression` on current `day_state` | 4.1, 4.9, 5.13, 5.14, 7.1, 16.3, 16.4 |
| Mark PUBLISHED idempotently | UpdateItem with `ConditionExpression: day_state <> :published` | 9.2, 9.4 |
| Increment attempt count | UpdateItem `ADD attempt_count :one` before each attempt | 12.7 |
| Status | Query partition, aggregate | 13.1, 13.2 |
| Complete campaign | UpdateItem campaign `campaign_state = COMPLETED` when published day == total_days | 15.1, 15.3 |

### Conditional Writes for Idempotency

Every state transition uses a `ConditionExpression` asserting the expected current state, which both enforces the state machine (Requirement 16) and provides idempotency. The PUBLISHED write condition `day_state <> PUBLISHED` guarantees a day is never published twice (Requirements 9.2, 9.4).

### TTL

TTL is not applied to campaign or day items because publication history must be retained (Requirements 13, 15). TTL is reserved for optional transient audit items only (for example a short-lived idempotency-marker item), and is documented as optional.

## State Machines

### Campaign_State transitions (Requirements 15, 16.1, 16.2)

| From | To | Trigger | Allowed |
| --- | --- | --- | --- |
| DRAFT | APPROVED | approve request | yes (Req 2.4, 16.1) |
| APPROVED | READY | auto after approve | yes (Req 2.8, 16.1) |
| READY | RUNNING | first scheduled or manual run | yes (Req 16.1) |
| RUNNING | PAUSED | pause request | yes (Req 14.1, 16.1) |
| READY | PAUSED | pause request | yes (Req 14.1) |
| PAUSED | RUNNING | resume request | yes (Req 14.3, 16.1) |
| RUNNING | COMPLETED | final day published | yes (Req 15.1, 16.1) |
| any other pair | - | any | rejected, state preserved, error names current and requested state (Req 16.2) |

Invalid examples that MUST be rejected: DRAFT to READY, COMPLETED to RUNNING, PAUSED to COMPLETED, APPROVED to PAUSED (approve request on a non-DRAFT campaign is likewise rejected per Req 2.5).

### Day_State transitions (Requirement 16.3, 16.4, 16.5, 16.6)

| From | To | Trigger | Allowed |
| --- | --- | --- | --- |
| PLANNED | GENERATING | day selected for generation | yes (Req 4.1) |
| GENERATING | GENERATED | generation succeeded | yes (Req 4.9) |
| GENERATED | READY | validation passed | yes (Req 5.13) |
| READY | PUBLISHING | publish begins | yes (Req 7.1) |
| PUBLISHING | PUBLISHED | publish succeeded | yes (Req 7.2) |
| GENERATING / GENERATED / READY / PUBLISHING | FAILED | failure at that stage | yes (Req 4.8, 5.14, 7.5, 7.6) |
| FAILED | GENERATING | retry request | yes (Req 16.5) |
| any other pair | - | any | rejected, state preserved, error names current and requested state (Req 16.4) |

Retry is permitted only from FAILED; a retry from any other state is rejected (Requirement 16.6).

## Idempotency Design

- Deterministic `Idempotency_Key` derived from `campaign_id` and `day` (for example `sha256(f"{campaign_id}#{day}")`). Identical inputs always produce the same key; any difference produces a different key (Requirement 9.1).
- The PUBLISHED transition uses a conditional write `ConditionExpression: day_state <> PUBLISHED` (Requirement 9.2).
- Prevented-duplicate handling: if a repeated trigger targets an already-PUBLISHED day, the Orchestrator performs no publication action, records exactly one prevented-duplicate entry (metric `DuplicateRunsPrevented`), and preserves the PUBLISHED state (Requirement 9.3).
- If the conditional write fails because the day is already PUBLISHED, the Orchestrator treats it as a prevented duplicate, does not retry the write, and ends within 5 seconds (Requirement 9.4).
- If the conditional write fails for another reason, the Orchestrator does not record PUBLISHED, retries up to 3 times with the same Idempotency_Key, and emits an error identifying the failed day if all attempts fail (Requirement 9.5).
- Ambiguous publication results are verified against the publication target before any additional publish attempt (Requirements 12.3, 12.4) via `BuilderCenterPublisher.verify_published`.

## Bedrock Design

Model recommendation: Amazon Nova Micro as the default, with Amazon Nova Lite and Anthropic Claude 3.5 Haiku as configurable alternatives. Rationale: Nova Micro is the lowest-cost understanding-model tier on Bedrock (about USD 0.035 per million input tokens per AWS Bedrock pricing, February 2026), which fits the sub-USD-5 target with wide headroom. Content was rephrased for compliance with licensing restrictions. Article generation is a short, well-scoped text task that does not require a frontier model. The model is configurable (Requirement 19.2), so the user can trade cost for quality by selecting Nova Lite or Claude 3.5 Haiku. Model availability varies by region; the deployment validates the configured model ID against the selected region before use, and states uncertainty rather than guessing when a model is unavailable.

Prompt artifacts:

- Prompts are versioned assets stored as files (for example `prompts/article_v1.md`) packaged with the Lambda, not inline string literals. Versioning enables auditability and safe iteration.
- The generation request carries: campaign_id, day, approved topic, difficulty `beginner_to_lower_intermediate`, `voice=first_person`, `executed_in_aws=false`, and length limits (title <= 255, description <= 512, tags <= 5) (Requirement 4.2).

Structured output contract (Requirement 4.3):

```json
{
  "title": "string (1..255)",
  "description": "string (1..512)",
  "body_markdown": "string (>= 1)",
  "tags": ["1..5 tags"],
  "learning_takeaway": "string (non-empty)"
}
```

Parsing and validation: the response is parsed as JSON; if it cannot be parsed into the required object, or any required field is missing/empty, or lengths/tag counts are out of range, the outcome is a generation failure and no article is produced (Requirement 4.6). A 120-second timeout bounds the call; timeout is a generation failure (Requirement 4.7).

Anti-fabrication constraints (Requirements 4.5, 20.2): the prompt instructs the model to write in first person, document learning honestly, avoid fabricated experience, certifications, deployed resources, measurements, costs, and screenshots, and to use portable fenced text diagrams only (Requirement 20.1). These are enforced deterministically afterward by the Content_Validator; the prompt reduces but does not replace validation.

Token and cost minimization: concise system prompt, bounded `max_tokens`, single generation call per day, and a small default model. Retries on transient Bedrock errors are bounded (see Error Handling) rather than open-ended.

## Content Validation Design

The Content_Validator runs deterministic checks and returns a `ValidationResult`. Each failed check appends a `ValidationViolation` with a stable `code`, the offending `field`, and a safe human-readable `message` (used in failure notifications per Requirement 5.14).

| Check | Rule | Requirement | Violation code |
| --- | --- | --- | --- |
| Title present and length | 1..255 after trim | 5.1, 5.2 | `TITLE_MISSING`, `TITLE_TOO_LONG` |
| Description present and length | 1..512 after trim | 5.3, 5.4 | `DESCRIPTION_MISSING`, `DESCRIPTION_TOO_LONG` |
| Body present | >= 1 char after trim | 5.5 | `BODY_EMPTY` |
| Markdown validity | parses with zero errors | 5.6, 5.7 | `MARKDOWN_INVALID` |
| Tag count | 1..5 inclusive | 5.8 | `TAG_COUNT_INVALID` |
| Placeholder text | no placeholders (e.g. `TODO`, `lorem ipsum`, `<...>`) | 5.9 | `PLACEHOLDER_PRESENT` |
| Fabricated execution | no executed-operation claim when `executed_in_aws` is false | 5.10 | `FABRICATED_EXECUTION` |
| Fabricated experience | no employment, projects, incidents, certifications, customer environments, or measurements | 20.3 | `FABRICATED_EXPERIENCE` |
| Topic match | article topic matches approved topic for the day | 5.11 | `TOPIC_MISMATCH` |
| Duplicate publication | no existing PUBLISHED day for same campaign_id and day | 5.12 | `DUPLICATE_PUBLICATION` |
| Non-portable diagram | no diagram syntax other than portable fenced text | 20.4 | `NON_PORTABLE_DIAGRAM` |

On success the Orchestrator sets Day_State to READY within 5 seconds (Requirement 5.13). On any failure the Orchestrator sets FAILED, retains the article without publishing, and triggers a failure notification listing every failed check (Requirement 5.14).

## API Design

Recommendation: authenticated Lambda Function URL with IAM auth (`AWS_IAM`).

Evaluation:

| Option | Auth | Cost | Fit |
| --- | --- | --- | --- |
| Lambda Function URL + IAM | SigV4 (IAM) | no per-request API charge; only Lambda invocation | best for a single-user, low-volume command surface |
| API Gateway HTTP API | JWT/IAM/Lambda authorizer | low but non-zero per-request | more features than needed for MVP |

Decision: Lambda Function URL with `AuthType = AWS_IAM`. For a single-user, low-frequency command surface this removes the API Gateway request charge entirely and relies on SigV4 IAM authentication, satisfying "no unauthenticated RUN" (Requirements 11.6, 11.7). API Gateway HTTP API remains a documented drop-in alternative if richer routing or a JWT authorizer is later required.

Cross-cutting API behavior:

- Every request is authenticated; unauthenticated requests are rejected before any downstream call (Requirements 11.6, 11.7, 13.4, 14.6).
- Payloads are validated before invoking any downstream component; invalid payloads are rejected without modifying state (Requirements 11.8, 11.9).
- Replay protection: requests carry a client-supplied nonce plus timestamp; the Command_API rejects stale timestamps (outside a small window) and reuses of a nonce within the window. Combined with SigV4, this protects the RUN and retry paths from replay (PRD section 17, Requirement 9 intent).
- Manual RUN for a campaign with no eligible day is rejected without invoking the Orchestrator (Requirement 11.5).

## Scheduling

- Amazon EventBridge Scheduler triggers the Orchestrator at the configured execution time (Requirements 11.1, 23).
- Schedules are timezone-aware and evaluated in the campaign timezone, defaulting to Africa/Nairobi (Requirements 11.2, 23, 1.2).
- Execution time, start date, timezone, and duration are configurable (Requirement 23).
- The Scheduler triggers only for campaigns in READY or RUNNING; a trigger reaching a campaign in any other state does not invoke the Orchestrator and records a skipped trigger (Requirement 11.3).
- EventBridge Scheduler is used instead of any always-on compute (Requirements 18.1, 18.3).

## Notifications

- A single SNS topic carries notifications; email is the primary subscription, SMS is optional and configured per campaign (Requirements 10.6, 24).
- Success notification includes campaign id, day, article title, status PUBLISHED, ISO-8601 UTC timestamp, and Builder Center URL when available; when the URL is unavailable the field is set to an explicit "unavailable" value and all other fields are still included (Requirements 10.1, 10.2).
- Failure notification includes campaign id, day, stage, status FAILED, sanitized error reason (<= 1024 chars), and a remediation action (Requirement 10.3).
- Secret exclusion: every field, including the sanitized reason, excludes passwords, cookies, access tokens, session headers, and personal authentication details (Requirement 10.4).
- Notification-failure isolation: SNS publish is retried up to 3 times; a notification failure never flips an already-successful publication to FAILED (Requirement 10.5).

## Security and IAM

Least-privilege IAM, scoped separately (Requirement 8.7):

| Permission set | Actions | Scope |
| --- | --- | --- |
| DynamoDB | GetItem, Query, PutItem, UpdateItem | the single table ARN only |
| Bedrock | InvokeModel | the configured model ARN only |
| Secrets Manager | GetSecretValue | the specific session secret ARN only |
| SNS | Publish | the specific topic ARN only |
| CloudWatch Logs | CreateLogStream, PutLogEvents | the function log group only |

Secret handling:

- Session material is stored in Secrets Manager, encrypted at rest, and resolved at runtime using `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` so the value never enters application context or logs (Requirements 8.2, 8.3; steering: secret safety).
- The Builder ID password is never stored in source, environment variables, logs, DynamoDB, or plaintext configuration (Requirement 8.1).
- If the secret read fails or returns nothing, the day is set FAILED with a remediation notification and no automatic retry of the retrieval (Requirement 8.4).
- Auth failure or session expiry sets the day FAILED with a remediation notification and no automatic retry (Requirements 8.5, 12.6).
- The engine never attempts to bypass authentication, MFA, CAPTCHA, bot detection, or rate limits (Requirement 8.6).

Additional controls:

- Input validation on all Command_API payloads (Requirements 11.8, 11.9).
- SSRF considerations: the Orchestrator makes no requests to user-supplied URLs; publication targets are fixed by the selected adapter, and any adapter network egress is restricted to known Builder Center hosts.
- Replay protection on state-changing endpoints (API Design section).

## Publishing Adapter Strategy

This is the highest-risk component and a first-class design decision.

Discovery finding (AWS sources, February to March 2026): there is no documented official or public AWS Builder Center article-publishing API. Builder Center is a signed-in web experience; AWS Builder ID is the credential and now supports federated sign-in and MFA, which increases automation risk and fragility. Content was rephrased for compliance with licensing restrictions.

1. Evaluation order for a real publisher:
   1. Official or supported Builder Center publishing API, if one exists.
   2. A supported authenticated integration mechanism.
   3. Controlled browser automation, only if permitted by Builder Center terms and only if technically reliable.
2. Blocker documentation: if no safe, reliable, permitted mechanism exists, the `BuilderCenterPublisher` remains a documented blocked task. The system stays fully functional on the `MockPublisher`, and the blocker (with findings and date) is recorded rather than worked around.
3. MVP default: the deployable MVP selects `MockPublisher` by configuration (`publisher_selection = mock`). This lets the full pipeline (schedule, generate, validate, record, notify) run end to end and stay under budget while the publishing mechanism is unresolved.
4. Browser automation candidate: headless Chromium via Playwright in a Lambda container image is a documented candidate but is NOT auto-selected. Its cost and reliability implications (larger image, longer cold start and runtime, higher Lambda cost, fragility against UI and bot-protection changes) are called out and would require an explicit configuration choice plus a passing reliability validation.
5. Fail-safe behavior: the adapter never bypasses MFA, CAPTCHA, bot detection, or rate limits. It detects authentication or session expiry and notifies for remediation rather than retrying (Requirements 8.5, 8.6, 12.6). Ambiguous results are verified before any re-publish (Requirements 12.3, 12.4).

The Orchestrator depends only on `publish_article(article) -> PublicationResult`, so swapping Mock for a future real publisher requires no change elsewhere (Requirement 6.4).


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system, essentially a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This engine is well suited to property-based testing because its core is pure logic with large input spaces: state machines, deterministic validation, idempotency keys, completion arithmetic, and redaction. The properties below were derived from the prework analysis and consolidated to remove redundancy. Each is universally quantified and traces to the requirements it validates. Infrastructure, scheduling delivery, and the discovery-dependent real publisher are covered by integration and smoke tests instead (see Testing Strategy).

### Property 1: Campaign state machine admits only allowed transitions

*For any* campaign in any Campaign_State and any requested target state, the Campaign_Manager accepts the transition if and only if the pair is one of DRAFT to APPROVED, APPROVED to READY, READY to RUNNING, READY to PAUSED, RUNNING to PAUSED, PAUSED to RUNNING, or RUNNING to COMPLETED; every other request is rejected with the current and requested state named, and the stored state is left unchanged.

**Validates: Requirements 2.4, 2.5, 2.8, 14.1, 14.3, 14.4, 14.5, 16.1, 16.2**

### Property 2: Day state machine admits only allowed transitions

*For any* Campaign_Day in any Day_State and any requested target state, the Orchestrator accepts the transition if and only if the pair is one of PLANNED to GENERATING, GENERATING to GENERATED, GENERATED to READY, READY to PUBLISHING, PUBLISHING to PUBLISHED, one of GENERATING/GENERATED/READY/PUBLISHING to FAILED, or FAILED to GENERATING; every other request is rejected with the current and requested state named, and the stored state is left unchanged.

**Validates: Requirements 4.1, 4.8, 4.9, 5.13, 7.1, 7.2, 7.5, 16.3, 16.4, 16.5, 16.6**

### Property 3: A campaign day is never published twice

*For any* sequence of executions (scheduled, manual, or retried) targeting a Campaign_Day, the day is transitioned to PUBLISHED at most once; once PUBLISHED, any further execution performs no publication action, records exactly one prevented-duplicate outcome per redundant execution, and preserves the PUBLISHED state and recorded publication data unchanged.

**Validates: Requirements 9.2, 9.3, 9.4, 12.1**

### Property 4: Idempotency key is deterministic and injective

*For any* pair of (campaign_id, day) inputs, deriving the Idempotency_Key twice from identical inputs yields identical keys, and deriving it from inputs that differ in campaign_id or day yields different keys.

**Validates: Requirements 9.1**

### Property 5: Conditional PUBLISHED write retries bounded on non-duplicate failure

*For any* sequence of conditional-write failures that are not caused by the day already being PUBLISHED, the Orchestrator never records the day as PUBLISHED, retries the write at most 3 times with the same Idempotency_Key, and emits an error identifying the failed day if all attempts fail.

**Validates: Requirements 9.5**

### Property 6: Deterministic validation soundness

*For any* generated article, the Content_Validator accepts it if and only if it satisfies every quality gate (title 1..255, description 1..512, non-empty body, valid Markdown, 1..5 tags, no placeholders, no fabricated execution claim when executed_in_aws is false, no fabricated experience, topic matches the approved topic, no existing PUBLISHED day for the same campaign_id and day, and only portable fenced text diagrams); and for any article that violates exactly one gate, the result is a rejection whose violations include the matching violation code for that gate.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 20.3, 20.4**

### Property 7: Validation failure marks FAILED, retains article, and reports every violation

*For any* article that fails one or more validation gates, the Orchestrator sets the Day_State to FAILED, retains the article without publishing it, and triggers a failure notification whose content lists every failed validation check.

**Validates: Requirements 5.14**

### Property 8: Bedrock response parsing produces a bounded structured article or a generation failure

*For any* Bedrock response, parsing either yields an Article whose title is 1..255 characters, description is 1..512 characters, body is non-empty, tags number 1..5, and learning_takeaway is non-empty, or it is classified as a generation failure that produces no article; and for any valid Article, serializing then parsing it preserves all fields.

**Validates: Requirements 4.3, 4.6**

### Property 9: Generation request carries the required first-person, bounded specification

*For any* generation specification, the request sent to Amazon Bedrock includes the approved topic, the day number, the campaign identifier, the Beginner to Lower-Intermediate difficulty, a first-person voice indicator, and the length limits (title at most 255, description at most 512, at most 5 tags).

**Validates: Requirements 4.2, 4.4**

### Property 10: PublicationResult honors its output contract

*For any* PublicationResult returned by any Publishing_Adapter: a success result carries a publication URL of length 1..2048 when a URL is available and an empty URL field when none is available; a failure result carries a failure status, an error summary containing no authentication material, and leaves the input article unchanged.

**Validates: Requirements 6.6, 6.7, 6.8**

### Property 11: Orchestrator behavior depends only on the shared publish contract

*For any* configured Publishing_Adapter implementation, replacing the implementation with any other that returns an equivalent PublicationResult produces identical Orchestrator handling, because the Orchestrator references only publish_article and PublicationResult.

**Validates: Requirements 6.4**

### Property 12: Eligible-day selection picks the lowest eligible day, preferring FAILED

*For any* collection of Campaign_Days, the Orchestrator selects the day with the lowest day number whose Day_State is PLANNED or FAILED, and when both a PLANNED and a FAILED day share that lowest number it selects the FAILED one; when no such day exists it selects none.

**Validates: Requirements 3.1, 3.2, 3.6**

### Property 13: Completion is monotonic in total_days and never hard-coded

*For any* campaign with a total day count between 1 and 365, the Campaign_State transitions to COMPLETED exactly when a Campaign_Day whose day number equals that total day count reaches PUBLISHED, the decision is computed by comparing the published day number against the configured total day count, and once COMPLETED any further publish request is rejected while preserving all published records.

**Validates: Requirements 3.6, 7.7, 15.1, 15.2, 15.3, 15.4**

### Property 14: Topic plans are well formed for any length

*For any* total day count between 1 and 365, the Topic_Planner produces a sequence whose length equals the total day count, associating exactly one non-empty topic with each day such that no two days share the same topic.

**Validates: Requirements 2.1, 2.2, 15.4**

### Property 15: No secret ever appears in logs, metrics, or notifications

*For any* execution and any log record, metric, or notification it emits, no field contains a password, cookie, access token, session header, or personal authentication detail in cleartext.

**Validates: Requirements 8.1, 8.3, 10.4, 17.4**

### Property 16: Redaction removes excluded values and records a valueless redaction event

*For any* log or metric payload that contains a value from an excluded category, the emitted output has that value removed and includes a redaction event indicating a sensitive value was removed, without the removed value appearing anywhere in the output.

**Validates: Requirements 17.5**

### Property 17: Correlation ID is unique per execution and attached everywhere

*For any* execution, a single Correlation_ID is generated, differs from the Correlation_ID of any other execution, and appears on every structured log record and metric emitted during that execution.

**Validates: Requirements 17.1**

### Property 18: Authentication is required before any downstream effect

*For any* Command_API request that is unauthenticated, the request is rejected with an authentication-required response, no downstream component is invoked, and no campaign state is created or modified.

**Validates: Requirements 11.6, 11.7, 13.4, 14.6**

### Property 19: Invalid payloads are rejected before any downstream effect

*For any* authenticated Command_API request whose payload fails validation, the request is rejected with a validation-failure response, no downstream component is invoked, and no campaign state is modified.

**Validates: Requirements 11.8, 11.9**

### Property 20: Auth and secret-retrieval failures fail safe without automatic retry

*For any* execution in which the secret read fails or returns no material, or an authentication failure or session expiry occurs during publication, the Orchestrator sets the Day_State to FAILED, triggers a failure notification containing remediation instructions, and performs no automatic retry of the retrieval or the authentication.

**Validates: Requirements 8.4, 8.5, 12.6**

### Property 21: Publication retries use bounded exponential backoff with attempt accounting

*For any* sequence of transient publication failures, the Orchestrator increments the attempt count by 1 before each attempt, applies exponential backoff starting at 1 second and doubling each attempt, makes at most 5 attempts, and on reaching 5 attempts without success sets FAILED and triggers a maximum-retry failure notification.

**Validates: Requirements 12.2, 12.5, 12.7**

### Property 22: Ambiguous results are verified before any re-publish

*For any* publication attempt returning an ambiguous result, the Orchestrator queries the publication target to determine whether the article was already published before making any further publish attempt, and if the query confirms prior publication it treats the outcome as successful and makes no further attempt.

**Validates: Requirements 12.3, 12.4**

### Property 23: Notification content is complete and bounded

*For any* Campaign_Day transition to PUBLISHED the success notification contains the campaign identifier, day identifier, article title, status PUBLISHED, an ISO-8601 UTC timestamp, and either the Builder Center URL or an explicit unavailable marker; and *for any* transition to FAILED the failure notification contains the campaign identifier, day identifier, stage, status FAILED, a sanitized reason of at most 1024 characters, and a remediation action.

**Validates: Requirements 10.1, 10.2, 10.3**

### Property 24: Notification failures never corrupt a successful publication

*For any* SNS publish failure occurring after a Campaign_Day has been recorded PUBLISHED, the notification is retried at most 3 times and the publication outcome remains PUBLISHED.

**Validates: Requirements 10.5**

### Property 25: Timezone-aware schedule evaluation

*For any* configured execution time, start date, and campaign timezone, the scheduled trigger instant equals the correct conversion of that local time in that timezone to UTC.

**Validates: Requirements 11.2**

### Property 26: Paused campaigns and ineligible schedules do not execute

*For any* campaign in Campaign_State PAUSED, or any scheduled trigger reaching a campaign whose state is neither READY nor RUNNING, the Orchestrator executes no Campaign_Day and a skipped trigger is recorded when applicable.

**Validates: Requirements 11.3, 14.2**

## Error Handling

Failures are categorized and handled by policy, driven by typed errors.

| Category | Examples | Handling | Requirements |
| --- | --- | --- | --- |
| Generation failure | Bedrock unavailable, malformed response, out-of-bounds fields, 120s timeout | Set Day_State FAILED, do not publish, notify | 4.6, 4.7, 4.8, 5.14 |
| Validation failure | any failed quality gate | Set FAILED, retain article, notify with every violation | 5.14 |
| Authentication failure | secret read fails/empty, session expiry, auth rejected | Set FAILED, notify with remediation, no automatic retry | 8.4, 8.5, 12.6 |
| Publication failure (transient) | timeout, retryable adapter error | Exponential backoff 1s doubling, at most 5 attempts, verify ambiguous results before re-publish, notify after final failure | 7.5, 7.6, 12.2, 12.3, 12.4, 12.5 |
| Idempotency conflict | conditional write blocked by existing PUBLISHED | Treat as prevented duplicate, no retry, end within 5s | 9.3, 9.4 |
| Infrastructure write failure (non-duplicate) | DynamoDB throttling on PUBLISHED write | Retry up to 3 times with same Idempotency_Key, error if all fail, never record PUBLISHED erroneously | 9.5 |
| Notification failure | SNS publish error | Retry up to 3 times, never flip a successful publication to FAILED | 10.5 |

Principles: never blindly retry authentication failures; never retry an ambiguous publication without verifying the target first; bound every retry loop.

## Cost Estimate

Approximate cost for a 21-day campaign with the MockPublisher / API-only path (US East pricing tier, on-demand). Figures are conservative order-of-magnitude estimates for the automation itself, excluding unrelated workloads.

| Service | Usage over 21 days | Estimated cost |
| --- | --- | --- |
| Lambda | ~21 scheduled runs plus a handful of command runs, each well under 60s at 256 MB | < USD 0.02 (within free-tier for most accounts) |
| EventBridge Scheduler | ~21 invocations | negligible (first 14M scheduler invocations priced in fractions of a cent) |
| DynamoDB on-demand | ~21 days x tens of small reads/writes each | < USD 0.05 |
| Amazon Bedrock (Nova Micro) | ~21 generations, each a few thousand input tokens and ~1-2k output tokens | < USD 0.10 at ~USD 0.035 per 1M input tokens |
| SNS | ~21 to 42 email/SMS publishes | email negligible; SMS a few cents each if enabled |
| CloudWatch Logs/Metrics | low-volume structured logs, minimal retention | < USD 0.10 |
| API layer (Lambda Function URL, IAM) | no per-request API charge | USD 0.00 |
| **Total (MockPublisher / API-only)** | | **well under USD 5.00** |

Largest cost uncertainty: browser-automation runtime. If a future `BuilderCenterPublisher` uses a Lambda container image with headless Chromium/Playwright, per-run duration and memory rise substantially (longer cold starts, higher memory tier, longer page interactions), which could dominate the profile. This path is not part of the MVP cost and is not auto-selected. The deployment includes a pre-deploy cost-estimate gate that fails deployment and names the projected total if it reaches USD 5.00 or more (Requirements 18.4, 18.5).

## Infrastructure as Code

Terraform defines all infrastructure (Requirement 19.1). Layout separates application code, infrastructure, and secrets:

```text
repo/
  app/                      # Python Lambda source (pure-logic modules + handlers)
    orchestrator/
    campaign/
    topics/
    generation/
    validation/
    publishing/             # adapter interface, MockPublisher, BuilderCenterPublisher (discovery)
    notify/
    common/                 # logging, redaction, errors, clock
    prompts/                # versioned prompt assets (article_v1.md, ...)
  infra/                    # Terraform
    main.tf                 # table, lambda, function url, scheduler, sns, iam, log group
    variables.tf            # required configuration variables
    outputs.tf
    modules/
  config/
    example.tfvars          # placeholders only, no secrets
  tests/
    unit/
    property/
    integration/
```

Configuration exposes campaign identifier, notification email, notification phone number, schedule, timezone, Bedrock model, AWS region, notification topic, and publishing configuration (Requirement 19.2). Secrets are excluded from source control; only Secrets Manager ARNs and placeholders appear in config (Requirements 8.1, 19.3). Missing required configuration fails deployment and names each missing value; invalid formats fail deployment and name each field with its expected format (Requirements 19.4, 19.5). `example.tfvars` contains placeholders only:

```hcl
campaign_id         = "aws-learning-21"
notification_email  = "you@example.com"
notification_phone  = ""                      # optional; empty disables SMS
schedule_time       = "09:00"
timezone            = "Africa/Nairobi"
start_date          = "2026-01-01"
total_days          = 21
bedrock_model_id    = "amazon.nova-micro-v1:0"
aws_region          = "us-east-1"
publisher_selection = "mock"                  # mock | builder_center
session_secret_arn  = "REPLACE_WITH_SECRET_ARN"  # value stored only in Secrets Manager
```

## Testing Strategy

Dual approach: property-based tests verify universal properties across many generated inputs; unit tests cover specific examples and edge cases; integration and smoke tests cover infrastructure and external behavior that PBT does not fit.

Property-based tests:

- Library: Hypothesis (Python). Each correctness property is implemented by a single property-based test.
- Minimum 100 iterations per property test.
- Each property test is tagged with a comment: **Feature: aws-builder-automation, Property {number}: {property_text}**.
- Coverage: Properties 1 through 26 above. Generators build campaigns, day collections, articles (valid and single-violation-invalid), Bedrock responses (well-formed, malformed, out-of-bounds), transition requests, payloads, and timezone/time inputs. External clients (DynamoDB, Bedrock, SNS, Secrets Manager) are injected as fakes so property tests stay fast and cheap.

Unit tests (specific examples and edge cases):

- Timestamp formatting (1.7), unique id assignment (1.8), topic-sequence edit replace (2.6), topic retrieval (3.3), generation timeout edge (4.7), prompt anti-fabrication and voice/diagram instructions (4.5, 20.1, 20.2), publish-op signature and MockPublisher no-network (6.1, 6.2), publication timeout edge (7.6), SMS channel (10.6), manual-run invocation (11.4), unknown-campaign status (13.3), out-of-range and missing/invalid config edges (1.3, 1.4, 1.6, 2.3, 6.5, 15.5, 18.5, 19.4, 19.5), cost-estimate gate (18.4).

Integration tests (real or emulated AWS, 1 to 3 examples each):

- DynamoDB conditional writes and single-table access patterns against DynamoDB Local.
- Bedrock invocation against the configured model in a test region (small, bounded).
- SNS publish to a test topic.
- Orchestrator end-to-end with MockPublisher selected: schedule to generate to validate to record to notify.
- BuilderCenterPublisher (6.3) is discovery-dependent and tested only if and when a permitted, reliable mechanism is confirmed.

Smoke tests (single execution) and IaC assertions:

- Terraform plan/synth assertions: DynamoDB on-demand (18.2), no always-on compute (18.1, 18.3), least-privilege IAM scoped per resource (8.7), Secrets Manager encryption (8.2), Terraform present and valid (19.1), config schema exposes required keys (19.2), no secrets in repo (19.3).

Publishing tests: all publishing-path testing in the MVP uses the MockPublisher so the full pipeline is exercised without contacting Builder Center.

Security checklist (verified in CI where possible):

- No password in source, env, logs, DynamoDB, or plaintext config (8.1) - covered by Property 15 plus repo scan.
- Secrets resolved at runtime only (8.3), never logged - Property 15.
- No control-bypass code paths in any adapter (8.6) - code review plus fail-safe tests.
- Auth required on every Command_API path (11.6, 11.7, 13.4, 14.6) - Property 18.
- Replay protection on state-changing endpoints - integration test.
