# Implementation Plan: AWS Builder Learning Automation Engine

## Overview

This plan implements the engine incrementally in Python 3.12 (Lambda application) with Terraform infrastructure and Hypothesis property-based tests, following the PRD phased development strategy. Each phase builds on the previous one and ends by wiring new code into the existing pipeline, so there is no orphaned code. The deployable MVP defaults to the MockPublisher via configuration and stays under USD 5; the real BuilderCenterPublisher is treated as a documented discovery task that is NOT assumed to work.

Testing is layered: 26 Hypothesis property-based tests (each tagged `Feature: aws-builder-automation, Property N`, minimum 100 iterations) validate universal properties; unit tests cover examples and edge cases; integration tests exercise DynamoDB Local, a Bedrock test region, and an SNS test topic; all publishing-path tests use the MockPublisher. Optional test sub-tasks are marked with `*`.

## Tasks

- [x] 1. Phase 0 - Foundation, project structure, and IaC scaffold
  - [x] 1.1 Create repository structure and Python packaging
    - Create `app/` packages (`orchestrator/`, `campaign/`, `topics/`, `generation/`, `validation/`, `publishing/`, `notify/`, `common/`, `prompts/`), plus `infra/`, `config/`, and `tests/{unit,property,integration}/`
    - Add `pyproject.toml` targeting Python 3.12 with dependencies (`boto3`, `markdown-it-py`, `hypothesis`, `pytest`, `mypy`) and configure `mypy` and `pytest`
    - _Requirements: 19.1_

  - [x] 1.2 Implement shared error types and clock abstraction in `app/common`
    - Define typed errors: `ValidationError`, `GenerationError`, `AuthenticationError`, `PublicationError`, `AmbiguousPublicationError`, `IdempotencyConflict`, `ConfigurationError`, `StateTransitionError`
    - Define an injectable `Clock` interface returning ISO-8601 UTC timestamps
    - _Requirements: 4.6, 4.7, 5.14, 8.4, 8.5, 12.6, 16.2, 16.4, 19.4_

  - [x] 1.3 Implement structured JSON logging, redaction, correlation IDs, and metrics in `app/common`
    - Emit structured JSON logs and named CloudWatch metrics (ArticlesGenerated, ArticlesPublished, GenerationFailures, PublicationFailures, AuthenticationFailures, DuplicateRunsPrevented, CampaignsCompleted)
    - Generate one unique `Correlation_ID` per execution and attach it to every log/metric
    - Redact excluded values (password, cookie, access token, session header, personal auth detail) and record a valueless redaction event
    - _Requirements: 8.1, 10.4, 17.1, 17.2, 17.3, 17.4, 17.5_

  - [x]* 1.4 Write property tests for correlation IDs and redaction
    - **Property 15: No secret ever appears in logs, metrics, or notifications**
    - **Property 16: Redaction removes excluded values and records a valueless redaction event**
    - **Property 17: Correlation ID is unique per execution and attached everywhere**
    - **Validates: Requirements 8.1, 8.3, 10.4, 17.1, 17.4, 17.5**

  - [x] 1.5 Scaffold Terraform infrastructure and configuration schema
    - Create `infra/{main.tf,variables.tf,outputs.tf}` scaffolding the DynamoDB table (on-demand), Lambda, Function URL, EventBridge Scheduler, SNS topic, IAM, and log group as declared but minimal resources
    - Expose config for campaign identifier, notification email, notification phone, schedule, timezone, Bedrock model, AWS region, notification topic, and publishing configuration; add `config/example.tfvars` with placeholders only (no secrets)
    - _Requirements: 18.1, 18.2, 18.3, 19.1, 19.2, 19.3_

  - [x]* 1.6 Write unit tests for configuration validation and IaC assertions
    - Test that missing required config fails and names each missing value; invalid formats fail and name each field with its expected format
    - Assert DynamoDB on-demand, no always-on compute, and no secrets present in repo config
    - _Requirements: 18.1, 18.2, 18.3, 19.2, 19.3, 19.4, 19.5_

- [x] 2. Phase 1 - DynamoDB campaign store and state machines
  - [x] 2.1 Implement single-table campaign store client in `app/campaign/store.py`
    - Implement GetItem, Query (`begins_with DAY#`, zero-padded keys), PutItem, and UpdateItem with `ConditionExpression` over an injected DynamoDB client for Campaign and Campaign_Day items
    - Implement conditional PUBLISHED write (`day_state <> PUBLISHED`) and `ADD attempt_count :one`
    - _Requirements: 1.1, 1.7, 3.1, 3.2, 9.2, 12.7, 13.1_

  - [x] 2.2 Implement Campaign and Day state-machine transition rules in `app/campaign/state.py`
    - Encode allowed Campaign_State and Day_State transitions; reject others with current and requested state named, preserving stored state
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6_

  - [x]* 2.3 Write property tests for the state machines
    - **Property 1: Campaign state machine admits only allowed transitions**
    - **Property 2: Day state machine admits only allowed transitions**
    - **Validates: Requirements 2.4, 2.5, 2.8, 4.1, 4.8, 4.9, 5.13, 7.1, 7.2, 7.5, 14.1, 14.3, 14.4, 14.5, 16.1, 16.2, 16.3, 16.4, 16.5, 16.6**

  - [ ]* 2.4 Write integration tests for the store against DynamoDB Local
    - Verify conditional writes, single-table access patterns, and lowest-eligible-day query ordering
    - _Requirements: 3.1, 3.2, 9.2, 13.1_

- [x] 3. Phase 2 - Campaign engine (creation, planning, approval, pause/resume, status, idempotency)
  - [x] 3.1 Implement CampaignManager creation and validation in `app/campaign/manager.py`
    - Validate name 1..200, description 0..1000, topic list 1..365, total day count 1..365, ISO-8601 start date, HH:MM execution time; default timezone Africa/Nairobi; assign unique id and created/updated timestamps; persist as DRAFT
    - Reject invalid requests naming every offending or missing field
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 15.5_

  - [x]* 3.2 Write unit tests for campaign creation edges
    - Test out-of-range/missing/oversized fields, timezone default, timestamp formatting, and unique id assignment
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 15.5_

  - [x] 3.3 Implement Topic_Planner in `app/topics/planner.py`
    - Produce an ordered sequence of length == total_days, one non-empty unique topic per day, generalized from a curated AWS fundamentals catalog with no hard-coded day-count-tied values; reject out-of-range counts naming the 1..365 range
    - _Requirements: 2.1, 2.2, 2.3, 15.4_

  - [x]* 3.4 Write property test for topic planning
    - **Property 14: Topic plans are well formed for any length**
    - **Validates: Requirements 2.1, 2.2, 15.4**

  - [x] 3.5 Implement approval, topic edit, pause, resume, and transition in CampaignManager
    - `submit_topics` replaces stored sequence only when length matches and each day has one non-empty topic, else rejects and retains prior sequence; `approve` DRAFT->APPROVED->READY within 5s; `pause`/`resume` per state rules; reject not-found/unauthorized
    - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 14.1, 14.3, 14.4, 14.5, 14.6_

  - [x] 3.6 Implement deterministic idempotency key derivation in `app/campaign/idempotency.py`
    - Derive `Idempotency_Key` as `sha256(f"{campaign_id}#{day}")`; identical inputs yield identical keys, differing inputs differ
    - _Requirements: 9.1_

  - [x]* 3.7 Write property test for the idempotency key
    - **Property 4: Idempotency key is deterministic and injective**
    - **Validates: Requirements 9.1**

  - [x] 3.8 Implement completion and status computation in CampaignManager
    - `complete_if_final` sets COMPLETED when published day == total_days (compared to configured value, no hard-coded count); reject publish requests when COMPLETED preserving records; support new campaign creation after completion
    - Compute status (current day, completed/failed counts, next/last execution timestamps, publication URLs, Campaign_State) returning empty/null fields rather than omitting; reject unknown/unauthenticated status
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 15.1, 15.2, 15.3, 15.4, 15.6_

  - [x]* 3.9 Write property test for completion and unit tests for status
    - **Property 13: Completion is monotonic in total_days and never hard-coded**
    - **Validates: Requirements 3.6, 7.7, 15.1, 15.2, 15.3, 15.4**
    - Unit tests: unknown-campaign status, empty/null status fields
    - _Requirements: 13.2, 13.3_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Phase 3 - Bedrock content generation and parsing
  - [x] 5.1 Define shared Article contract and versioned prompt asset
    - Add frozen `Article` dataclass in `app/generation/models.py` and store `app/prompts/article_v1.md` with first-person, anti-fabrication, portable-fenced-diagram, and beginner-friendly voice instructions
    - _Requirements: 4.3, 4.4, 4.5, 20.1, 20.2_

  - [x] 5.2 Implement ContentGenerator in `app/generation/generator.py`
    - Build the generation request carrying approved topic, day, campaign id, difficulty beginner_to_lower_intermediate, first-person voice, executed_in_aws=false, and length limits; invoke Bedrock (injected client) with a 120s timeout; parse JSON into a bounded `Article` or raise `GenerationError`
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x]* 5.3 Write property tests for generation
    - **Property 8: Bedrock response parsing produces a bounded structured article or a generation failure**
    - **Property 9: Generation request carries the required first-person, bounded specification**
    - **Validates: Requirements 4.2, 4.3, 4.4, 4.6**

  - [x]* 5.4 Write unit tests for generation edges and prompt content
    - Test 120s timeout classification and prompt anti-fabrication/voice/diagram instructions
    - _Requirements: 4.5, 4.7, 20.1, 20.2_

  - [ ]* 5.5 Write integration test for Bedrock invocation
    - Invoke the configured model in a test region with a small, bounded prompt and assert a parseable structured response
    - _Requirements: 4.2, 4.3_

- [x] 6. Phase 4 - Deterministic content validation
  - [x] 6.1 Implement ContentValidator in `app/validation/validator.py`
    - Implement all deterministic gates returning a `ValidationResult` with a violation code per failed check: title 1..255, description 1..512, non-empty body, valid Markdown, 1..5 tags, no placeholders, no fabricated execution when executed_in_aws is false, no fabricated experience, topic match, duplicate-publication (existing PUBLISHED day), and portable-fenced-diagram only
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 20.3, 20.4_

  - [x]* 6.2 Write property test for validation soundness
    - **Property 6: Deterministic validation soundness**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 20.3, 20.4**

- [x] 7. Phase 5 - Publishing adapter interface and MockPublisher
  - [x] 7.1 Define PublishingAdapter interface and PublicationResult contract in `app/publishing/adapter.py`
    - Define the `Protocol` with a single `publish_article(article) -> PublicationResult` and the frozen `PublicationResult` (status, publication_url, error_summary, ambiguous)
    - _Requirements: 6.1, 6.6, 6.7, 6.8_

  - [x] 7.2 Implement MockPublisher and config-based adapter selection in `app/publishing/`
    - MockPublisher returns a PublicationResult with no network to Builder Center; selection requires exactly one implementation, else `ConfigurationError` blocking any publish attempt
    - _Requirements: 6.2, 6.4, 6.5_

  - [x] 7.3 Add BuilderCenterPublisher as a documented discovery/blocked stub in `app/publishing/builder_center.py`
    - Provide `publish_article` and `verify_published` signatures with a documented blocker (no permitted, reliable Builder Center publishing mechanism confirmed); fail safe with `AuthenticationError` on auth/session issues and never bypass MFA/CAPTCHA/bot detection/rate limits. NOT assumed to work; not auto-selected
    - Non-coding sub-note: a real Builder Center integration requires a one-time manual Builder ID sign-in/session capture and confirmation that automation is permitted; this manual step is out of scope for coding tasks
    - _Requirements: 6.3, 8.6, 12.3, 12.4_

  - [x]* 7.4 Write property and unit tests for the publishing adapter
    - **Property 10: PublicationResult honors its output contract**
    - **Property 11: Orchestrator behavior depends only on the shared publish contract**
    - **Validates: Requirements 6.4, 6.6, 6.7, 6.8**
    - Unit tests: publish-op single-object signature (6.1), MockPublisher no-network (6.2), invalid publisher configuration rejection (6.5)
    - _Requirements: 6.1, 6.2, 6.5_

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Phase 6 - SNS notifications
  - [x] 9.1 Implement Notifier in `app/notify/notifier.py`
    - `notify_success` includes campaign id, day, title, status PUBLISHED, ISO-8601 UTC timestamp, and Builder Center URL or explicit unavailable marker; `notify_failure` includes campaign id, day, stage, status FAILED, sanitized reason <= 1024 chars, and remediation; exclude all secret material; optional SMS channel; retry SNS up to 3 times without flipping a successful publication to FAILED
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x]* 9.2 Write property tests for notifications
    - **Property 23: Notification content is complete and bounded**
    - **Property 24: Notification failures never corrupt a successful publication**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.5**

  - [x]* 9.3 Write unit and integration tests for notifications
    - Unit: SMS channel behavior (10.6); Integration: SNS publish to a test topic
    - _Requirements: 10.5, 10.6_

- [x] 10. Phase 7 - Orchestrator wiring the daily execution flow
  - [x] 10.1 Implement eligible-day selection in the Orchestrator (`app/orchestrator/orchestrator.py`)
    - Inject store, generator, validator, publisher, notifier, and clock; select the lowest-numbered PLANNED or FAILED day preferring FAILED; handle missing approved topic and no-eligible-day cases without publishing and without changing state; treat highest published >= total_days as complete
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x]* 10.2 Write property test for eligible-day selection
    - **Property 12: Eligible-day selection picks the lowest eligible day, preferring FAILED**
    - **Validates: Requirements 3.1, 3.2, 3.6**

  - [x] 10.3 Wire generation and validation stages into the Orchestrator
    - Set GENERATING before generation, GENERATED on success, FAILED on generation failure without publishing; on validation set READY within 5s, else FAILED retaining the article and triggering a failure notification listing every violation
    - _Requirements: 4.1, 4.8, 4.9, 5.13, 5.14_

  - [x]* 10.4 Write property test for validation-failure handling
    - **Property 7: Validation failure marks FAILED, retains article, and reports every violation**
    - **Validates: Requirements 5.14**

  - [x] 10.5 Wire publication, idempotency, and outcome recording into the Orchestrator
    - Set PUBLISHING before invoking the configured adapter; on success within 60s set PUBLISHED via conditional write, record publication URL and published_at, and advance/complete; on failure/timeout set FAILED with sanitized error (<= 500 chars) retaining article; handle prevented-duplicate (record one DuplicateRunsPrevented, end within 5s) and non-duplicate write failures (retry up to 3 times, error if all fail)
    - _Requirements: 6.4, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 9.2, 9.3, 9.4, 9.5_

  - [x]* 10.6 Write property tests for idempotency and conditional writes
    - **Property 3: A campaign day is never published twice**
    - **Property 5: Conditional PUBLISHED write retries bounded on non-duplicate failure**
    - **Validates: Requirements 9.2, 9.3, 9.4, 9.5, 12.1**

  - [x] 10.7 Wire retry, backoff, ambiguous-result verification, and auth fail-safe into the Orchestrator
    - Increment attempt count before each attempt; retry transient publication failures with exponential backoff (1s doubling, max 5) then FAILED + max-retry notification; verify ambiguous results via `verify_published` before any re-publish; on secret-read/auth/session failure set FAILED with remediation notification and no automatic retry
    - _Requirements: 8.4, 8.5, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_

  - [x]* 10.8 Write property tests for retries, ambiguous results, and auth fail-safe
    - **Property 20: Auth and secret-retrieval failures fail safe without automatic retry**
    - **Property 21: Publication retries use bounded exponential backoff with attempt accounting**
    - **Property 22: Ambiguous results are verified before any re-publish**
    - **Validates: Requirements 8.4, 8.5, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7**

  - [x]* 10.9 Write unit test for publication timeout edge
    - Test 60s publication timeout sets FAILED with a timeout error summary
    - _Requirements: 7.6_

- [x] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Phase 8 - EventBridge Scheduler and authenticated Command_API
  - [x] 12.1 Implement timezone-aware schedule evaluation in `app/orchestrator/schedule.py`
    - Convert configured execution time, start date, and campaign timezone (default Africa/Nairobi) to the correct UTC trigger instant; trigger only for READY or RUNNING campaigns, else record a skipped trigger; do not execute PAUSED campaigns
    - _Requirements: 11.1, 11.2, 11.3, 14.2_

  - [x]* 12.2 Write property tests for scheduling
    - **Property 25: Timezone-aware schedule evaluation**
    - **Property 26: Paused campaigns and ineligible schedules do not execute**
    - **Validates: Requirements 11.2, 11.3, 14.2**

  - [x] 12.3 Implement Command_API handler with IAM auth, validation, and replay protection in `app/orchestrator/command_api.py`
    - Route create, approve, run, retry, status, pause, resume; require authentication (SigV4/IAM) and reject unauthenticated requests before any downstream call; validate payloads before downstream calls without modifying state; enforce nonce+timestamp replay protection; reject manual run when no eligible day exists without invoking the Orchestrator
    - _Requirements: 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_

  - [x]* 12.4 Write property tests for API authentication and validation
    - **Property 18: Authentication is required before any downstream effect**
    - **Property 19: Invalid payloads are rejected before any downstream effect**
    - **Validates: Requirements 11.6, 11.7, 11.8, 11.9, 13.4, 14.6**

  - [x] 12.5 Complete Terraform IaC, least-privilege IAM, Secrets Manager, and Lambda Function URL
    - Wire Lambda handlers to EventBridge Scheduler and a Function URL (`AuthType = AWS_IAM`); scope IAM separately for DynamoDB, Bedrock InvokeModel, Secrets Manager GetSecretValue, SNS Publish, and CloudWatch Logs; store session material in Secrets Manager encrypted at rest resolved at runtime via `{{resolve:secretsmanager:...}}`
    - _Requirements: 8.2, 8.3, 8.7, 11.1, 18.1, 18.3, 19.1_

  - [x]* 12.6 Write IaC and security-checklist verification tests
    - Verify DynamoDB on-demand, no always-on compute, least-privilege IAM scoped per resource, Secrets Manager encryption, Terraform valid, config schema exposes required keys, no password/secret in source/env/logs/DynamoDB/config, auth required on every path, and replay protection
    - _Requirements: 8.1, 8.2, 8.3, 8.6, 8.7, 11.6, 11.7, 13.4, 14.6, 18.1, 18.2, 18.3, 19.1, 19.2, 19.3_

  - [x] 12.7 Implement pre-deploy cost-estimate gate in `infra/` (script) and config default to MockPublisher
    - Compute the projected 21-day cost and fail deployment naming the projected total if it reaches USD 5.00 or more; ensure `publisher_selection` defaults to `mock` so the MVP stays under USD 5
    - _Requirements: 6.4, 18.4, 18.5_

  - [x]* 12.8 Write unit test for the cost-estimate gate
    - Test that a projected total >= USD 5.00 fails and names the projected total, and the MockPublisher/API-only path passes
    - _Requirements: 18.4, 18.5_

- [x] 13. Phase 9 - End-to-end and duplicate/retry verification
  - [x] 13.1 Wire the full daily execution flow behind Scheduler and Command_API entry points
    - Connect schedule/command entry points through eligible-day selection, generation, validation, publication (MockPublisher), recording, completion, and notification with correlation IDs throughout; no orphaned code paths
    - _Requirements: 3.1, 4.1, 5.13, 7.7, 10.1, 11.1, 11.4, 15.1, 17.1_

  - [x]* 13.2 Write end-to-end integration test with MockPublisher
    - Drive schedule -> generate -> validate -> record -> notify against DynamoDB Local and an SNS test topic; assert PUBLISHED state, recorded URL/timestamp, and success notification
    - _Requirements: 7.2, 7.3, 7.4, 10.1, 15.1_

  - [x]* 13.3 Write integration test for duplicate prevention and retry recovery
    - Repeat execution against an already-PUBLISHED day (assert one DuplicateRunsPrevented, state unchanged); retry a FAILED day (assert re-execution without duplicate article)
    - _Requirements: 9.3, 9.4, 12.1, 16.5_

- [x] 14. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirement sub-clauses for traceability, and property test tasks name the exact design Property number(s) they validate.
- All 26 correctness properties are implemented as Hypothesis property-based tests, each tagged `Feature: aws-builder-automation, Property N` with a minimum of 100 iterations.
- The deployable MVP defaults to MockPublisher via configuration and stays under USD 5; BuilderCenterPublisher is a documented discovery/blocked task that is NOT assumed to work.
- The one-time Builder Center manual sign-in/session capture is a non-coding step noted under task 7.3 and is not a checkbox task.
- Integration tests use DynamoDB Local, a Bedrock test region, and an SNS test topic; all publishing-path tests use the MockPublisher.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.5"] },
    { "id": 2, "tasks": ["1.3", "1.6", "2.1", "2.2"] },
    { "id": 3, "tasks": ["1.4", "2.3", "2.4", "3.1", "3.3", "3.6", "5.1", "6.1", "7.1"] },
    { "id": 4, "tasks": ["3.2", "3.4", "3.5", "3.7", "5.2", "6.2", "7.2", "7.3", "9.1"] },
    { "id": 5, "tasks": ["3.8", "5.3", "5.4", "5.5", "7.4", "9.2", "9.3", "10.1"] },
    { "id": 6, "tasks": ["3.9", "10.2", "10.3", "12.1"] },
    { "id": 7, "tasks": ["10.4", "10.5", "12.2", "12.3"] },
    { "id": 8, "tasks": ["10.6", "10.7", "12.4", "12.5", "12.7"] },
    { "id": 9, "tasks": ["10.8", "10.9", "12.6", "12.8", "13.1"] },
    { "id": 10, "tasks": ["13.2", "13.3"] }
  ]
}
```
