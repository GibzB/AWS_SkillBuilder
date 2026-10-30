# Requirements Document

## Introduction

The AWS Builder Learning Automation Engine is a low-cost, serverless, AWS-native system that manages a reusable series of AWS Builder Center articles based on a user's AWS learning journey. A user defines and approves learning topics once, and the engine maintains a campaign schedule, generates a first-person article per campaign day using Amazon Bedrock, validates the content deterministically, publishes it through an isolated publishing adapter, notifies the user through Amazon SNS, and retains campaign and publication history.

The engine MUST be reusable for arbitrary campaign lengths (for example 21, 30, 60, 90, or custom day counts) with no hard-coded day count or topic values. Campaign completion is determined by comparing the current day against the campaign total day count. The system prioritizes low cost (target under USD 5 for a 21-day campaign), simplicity, security, observability, and reliable idempotent execution.

The Builder Center publishing mechanism is treated as a discovery-first, highest-risk component and is isolated behind a publishing adapter interface with both a mock implementation and a real implementation. The system never stores the Builder ID password, never exposes secrets, and never attempts to bypass authentication or bot-protection controls.

## Glossary

- **Engine**: The complete AWS Builder Learning Automation Engine described by these requirements.
- **Orchestrator**: The AWS Lambda function that coordinates a single campaign-day execution (topic selection, generation, validation, publication, state update, notification).
- **Campaign**: A configured, ordered series of learning days with a defined total day count, schedule, and topic list, persisted in DynamoDB.
- **Campaign_Day**: A single day entry within a campaign, identified by the composite key campaign_id + day, with its own state and article fields.
- **Campaign_Manager**: The component that creates, approves, and manages campaign lifecycle and state transitions.
- **Topic_Planner**: The component that produces a proposed ordered sequence of AWS learning topics for a campaign.
- **Content_Generator**: The Amazon Bedrock generation layer that transforms an approved topic into a structured article.
- **Content_Validator**: The deterministic validation component that checks generated articles against publication quality gates.
- **Publishing_Adapter**: The interface that isolates Builder Center authentication and submission behavior, exposing publish_article(article) returning a PublicationResult.
- **MockPublisher**: A Publishing_Adapter implementation that simulates publication for testing without contacting Builder Center.
- **BuilderCenterPublisher**: A Publishing_Adapter implementation that submits articles to AWS Builder Center using the permitted authenticated mechanism.
- **PublicationResult**: The structured outcome returned by a Publishing_Adapter, including status, publication URL when available, and error information when failed.
- **Builder_Center**: AWS Builder Center, the destination where articles are created and published by a signed-in user.
- **Builder_ID**: The credential a user employs to sign in to Builder Center.
- **Scheduler**: Amazon EventBridge Scheduler, responsible for triggering daily execution.
- **Notifier**: The component that publishes execution notifications through Amazon SNS.
- **Secret_Store**: AWS Secrets Manager, used to store supported sensitive session material.
- **Command_API**: The authenticated request interface (Lambda Function URL or API Gateway HTTP API) exposing campaign operations.
- **Correlation_ID**: A unique identifier attached to logs and metrics for a single execution to enable tracing.
- **Idempotency_Key**: A deterministic key derived from campaign_id + day that prevents duplicate publication of the same campaign day.
- **Campaign_State**: One of DRAFT, APPROVED, READY, RUNNING, PAUSED, COMPLETED.
- **Day_State**: One of PLANNED, GENERATING, GENERATED, READY, PUBLISHING, PUBLISHED, FAILED.

## Requirements

### Requirement 1: Campaign Creation

**User Story:** As an AWS builder, I want to create a campaign with configurable properties, so that I can define a reusable learning-to-content series.

#### Acceptance Criteria

1. WHEN a campaign creation request is received containing a name of 1 to 200 characters, a description of 0 to 1000 characters, a total day count, a start date in ISO-8601 date format, an execution time in 24-hour HH:MM format, an optional timezone, a topic list of 1 to 365 entries, a content style, and notification settings, THE Campaign_Manager SHALL persist a new Campaign to DynamoDB with Campaign_State set to DRAFT.
2. WHEN a campaign is created without an explicit timezone, THE Campaign_Manager SHALL set the campaign timezone to Africa/Nairobi.
3. IF a campaign creation request specifies a total day count less than 1, THEN THE Campaign_Manager SHALL reject the request and return a validation error identifying the invalid total day count.
4. IF a campaign creation request specifies a total day count greater than 365, THEN THE Campaign_Manager SHALL reject the request and return a validation error identifying the invalid total day count.
5. IF a campaign creation request omits a required field, THEN THE Campaign_Manager SHALL reject the request and return a validation error identifying each missing required field.
6. IF a campaign creation request provides a name, description, or topic list that exceeds its permitted length, THEN THE Campaign_Manager SHALL reject the request and return a validation error identifying each field that exceeds its permitted length.
7. WHEN a campaign is persisted, THE Campaign_Manager SHALL record a created timestamp and an updated timestamp in ISO-8601 format.
8. WHEN a campaign is created, THE Campaign_Manager SHALL assign a unique campaign identifier to the Campaign.

### Requirement 2: Topic Planning and Approval

**User Story:** As an AWS builder, I want the system to propose a topic sequence that I can review and approve, so that I control the learning journey before generation begins.

#### Acceptance Criteria

1. WHEN topic planning is requested for a campaign, THE Topic_Planner SHALL produce a proposed ordered sequence of AWS learning topics whose length equals the campaign total day count.
2. WHEN a proposed topic sequence is produced, THE Topic_Planner SHALL associate exactly one non-empty topic with each Campaign_Day from day 1 through the campaign total day count, such that no two Campaign_Days reference the same topic.
3. IF topic planning is requested for a campaign whose total day count is less than 1 or greater than 365, THEN THE Topic_Planner SHALL reject the request and return an error identifying the out-of-range total day count and the permitted range of 1 to 365.
4. WHEN a campaign owner submits an approval request for a campaign in Campaign_State DRAFT, THE Campaign_Manager SHALL set the Campaign_State to APPROVED within 5 seconds of receiving the request.
5. IF an approval request is received for a campaign that is not in Campaign_State DRAFT, THEN THE Campaign_Manager SHALL reject the request, preserve the existing Campaign_State, and return an error identifying the current Campaign_State.
6. WHEN a campaign owner submits an edited topic sequence for a campaign in Campaign_State DRAFT, THE Campaign_Manager SHALL replace the stored topic sequence with the submitted sequence.
7. IF an edited topic sequence is submitted whose length does not equal the campaign total day count, or which does not associate exactly one non-empty topic with each Campaign_Day, THEN THE Campaign_Manager SHALL reject the submission, retain the previously stored topic sequence, and return an error identifying the mismatch.
8. WHEN a campaign transitions to Campaign_State APPROVED, THE Campaign_Manager SHALL set the Campaign_State to READY.

### Requirement 3: Daily Topic Selection

**User Story:** As an AWS builder, I want each execution to select exactly one eligible day, so that one article is generated per run in sequence.

#### Acceptance Criteria

1. WHEN an execution begins for a campaign, THE Orchestrator SHALL select exactly one eligible Campaign_Day whose Day_State is PLANNED or FAILED and whose day number is the lowest such number among all eligible Campaign_Days in the campaign.
2. WHEN more than one Campaign_Day shares the lowest eligible day number, THE Orchestrator SHALL select the Campaign_Day whose Day_State is FAILED before selecting one whose Day_State is PLANNED.
3. WHEN an eligible Campaign_Day is selected, THE Orchestrator SHALL retrieve the approved topic associated with the selected Campaign_Day within 30 seconds.
4. IF an eligible Campaign_Day is selected but no approved topic is associated with it, THEN THE Orchestrator SHALL record an error indicating that the approved topic is missing and SHALL end the execution without publishing, leaving the Day_State of the selected Campaign_Day unchanged.
5. IF no Campaign_Day is eligible for a campaign whose highest published day number is less than the campaign total day count, THEN THE Orchestrator SHALL record that no eligible day was found and SHALL end the execution without publishing, leaving all Campaign_Day states unchanged.
6. IF an execution begins for a campaign whose highest published day number is greater than or equal to the campaign total day count, THEN THE Orchestrator SHALL not select any Campaign_Day and SHALL treat the campaign as complete.

### Requirement 4: Content Generation

**User Story:** As an AWS builder, I want Amazon Bedrock to generate a first-person article from the approved topic, so that content reflects my authentic learning voice.

#### Acceptance Criteria

1. WHEN a Campaign_Day is selected for generation, THE Orchestrator SHALL set the Day_State to GENERATING before invoking the Content_Generator.
2. WHEN the Content_Generator is invoked, THE Content_Generator SHALL send the approved topic, day number, campaign identifier, difficulty level of Beginner to Lower-Intermediate, first-person voice indicator, and article length limits of title at most 255 characters, description at most 512 characters, and at most 5 tags to Amazon Bedrock.
3. WHEN Amazon Bedrock returns a response, THE Content_Generator SHALL parse the response into a structured object containing a non-empty title of at most 255 characters, a non-empty description of at most 512 characters, a non-empty body_markdown, a tags collection of 1 to 5 tags, and a non-empty learning_takeaway.
4. THE Content_Generator SHALL request first-person perspective content for every generated article.
5. THE Content_Generator SHALL request content that excludes fabricated personal experience, certifications, deployed resources, measurements, costs, and screenshots.
6. IF Amazon Bedrock returns a response that cannot be parsed into the required structured object, or returns a structured object in which any required field is missing or empty, the title exceeds 255 characters, the description exceeds 512 characters, or the tags collection contains fewer than 1 or more than 5 tags, THEN THE Content_Generator SHALL classify the outcome as a generation failure and SHALL NOT produce an article.
7. IF Amazon Bedrock does not return a response within 120 seconds of invocation, THEN THE Content_Generator SHALL classify the outcome as a generation failure and SHALL NOT produce an article.
8. IF the Content_Generator classifies the outcome as a generation failure, THEN THE Orchestrator SHALL set the Day_State to FAILED and SHALL NOT publish an article for that Campaign_Day.
9. WHEN the Content_Generator produces a structured article, THE Orchestrator SHALL set the Day_State to GENERATED.

### Requirement 5: Deterministic Content Validation

**User Story:** As an AWS builder, I want deterministic validation before publication, so that only compliant, non-duplicate, on-topic articles are published.

#### Acceptance Criteria

1. WHEN a generated article is submitted for validation, THE Content_Validator SHALL confirm the title is present and contains between 1 and 255 characters inclusive after leading and trailing whitespace is removed.
2. IF the title is absent or contains 0 characters after leading and trailing whitespace is removed, or contains more than 255 characters, THEN THE Content_Validator SHALL reject the article and identify the title length violation.
3. WHEN a generated article is submitted for validation, THE Content_Validator SHALL confirm the description is present and contains between 1 and 512 characters inclusive after leading and trailing whitespace is removed.
4. IF the description is absent or contains 0 characters after leading and trailing whitespace is removed, or contains more than 512 characters, THEN THE Content_Validator SHALL reject the article and identify the description length violation.
5. WHEN a generated article is submitted for validation, THE Content_Validator SHALL confirm the body is present and contains at least 1 character after leading and trailing whitespace is removed.
6. WHEN a generated article is submitted for validation, THE Content_Validator SHALL confirm the body parses as valid Markdown with zero parsing errors.
7. IF the body fails to parse as valid Markdown, THEN THE Content_Validator SHALL reject the article and identify the Markdown parsing violation.
8. WHEN a generated article is submitted for validation, THE Content_Validator SHALL confirm the article contains between 1 and 5 tags inclusive.
9. IF a generated article contains placeholder text, THEN THE Content_Validator SHALL reject the article and identify the placeholder violation.
10. IF a generated article contains a claim that an AWS operation was executed when the execution indicator is false, THEN THE Content_Validator SHALL reject the article and identify the fabricated execution claim.
11. IF the topic of a generated article does not match the approved topic for the selected Campaign_Day, THEN THE Content_Validator SHALL reject the article and identify the topic mismatch.
12. IF a Campaign_Day with Day_State PUBLISHED already exists for the same campaign_id and day, THEN THE Content_Validator SHALL reject the article as a duplicate publication and identify the duplicate publication violation.
13. WHEN a generated article passes all validation checks, THE Orchestrator SHALL set the Day_State to READY within 5 seconds.
14. IF a generated article fails any validation check, THEN THE Orchestrator SHALL set the Day_State to FAILED, SHALL retain the article without publishing it, and SHALL trigger a failure notification that identifies each failed validation check.

### Requirement 6: Publishing Adapter Isolation

**User Story:** As an AWS builder, I want Builder Center publishing isolated behind an adapter interface, so that the publishing mechanism can be replaced without changing the rest of the system.

#### Acceptance Criteria

1. THE Publishing_Adapter SHALL expose exactly one publish operation that accepts a single article object as input and returns exactly one PublicationResult object as output.
2. WHEN the MockPublisher implementation of the Publishing_Adapter receives a publish request, THE MockPublisher SHALL return a PublicationResult without transmitting any network request to Builder_Center.
3. WHEN the BuilderCenterPublisher implementation of the Publishing_Adapter receives a publish request, THE BuilderCenterPublisher SHALL submit the article to Builder_Center through the permitted authenticated mechanism.
4. WHERE exactly one publishing implementation is selected through configuration, THE Orchestrator SHALL invoke that configured Publishing_Adapter implementation using only the shared publish operation and PublicationResult, without referencing any field or behavior specific to a single implementation.
5. IF no publishing implementation is selected through configuration, or more than one is selected, THEN THE Orchestrator SHALL reject the publish request, return an error summary indicating the configuration is invalid, and make no publish attempt.
6. WHEN a Publishing_Adapter completes a publish attempt with a success status, THE Publishing_Adapter SHALL return a PublicationResult containing a success status and a publication URL of 1 to 2048 characters.
7. WHEN a Publishing_Adapter completes a publish attempt that succeeds but no publication URL is available, THE Publishing_Adapter SHALL return a PublicationResult containing a success status and an empty publication URL field.
8. IF a Publishing_Adapter attempt fails, THEN THE Publishing_Adapter SHALL return a PublicationResult containing a failure status and an error summary that excludes all authentication material, and SHALL retain the input article unchanged.

### Requirement 7: Publication Execution

**User Story:** As an AWS builder, I want validated articles submitted and results recorded, so that publication outcomes are captured accurately.

#### Acceptance Criteria

1. WHEN a Campaign_Day reaches Day_State READY, THE Orchestrator SHALL set the Day_State to PUBLISHING before invoking the Publishing_Adapter.
2. WHEN the Publishing_Adapter returns a PublicationResult with a successful status within 60 seconds of invocation, THE Orchestrator SHALL set the Day_State to PUBLISHED.
3. WHEN the Publishing_Adapter returns a PublicationResult with a successful status that contains a publication URL, THE Orchestrator SHALL record the publication URL.
4. WHEN a Campaign_Day reaches Day_State PUBLISHED, THE Orchestrator SHALL record a published timestamp in ISO-8601 format with UTC timezone.
5. IF the Publishing_Adapter returns a PublicationResult with a failed status, THEN THE Orchestrator SHALL set the Day_State to FAILED and SHALL record the sanitized error summary truncated to a maximum of 500 characters, and SHALL retain the article content without modification.
6. IF the Publishing_Adapter does not return a PublicationResult within 60 seconds of invocation, THEN THE Orchestrator SHALL set the Day_State to FAILED and SHALL record an error summary indicating a publication timeout.
7. WHEN a Campaign_Day transitions to PUBLISHED and the publication URL and published timestamp have been recorded, THE Orchestrator SHALL advance the campaign to evaluate the next eligible day.

### Requirement 8: Authentication and Secret Safety

**User Story:** As an AWS builder, I want authentication handled securely, so that my Builder ID password and session material are never exposed or misused.

#### Acceptance Criteria

1. THE Engine SHALL exclude the Builder_ID password from source code, Lambda environment variables, logs, DynamoDB, and plaintext configuration such that no scan of these five locations returns the password value in cleartext.
2. WHERE sensitive session material is technically supported for storage, THE Engine SHALL store the session material in the Secret_Store encrypted at rest.
3. WHEN the BuilderCenterPublisher requires authentication material, THE BuilderCenterPublisher SHALL retrieve the material from the Secret_Store at runtime such that the material is held only in transient memory for the duration of the publication attempt and is not written to logs, DynamoDB, or plaintext configuration.
4. IF the Secret_Store read fails or returns no authentication material when the BuilderCenterPublisher requests it, THEN THE Orchestrator SHALL set the Day_State to FAILED, SHALL trigger a failure notification containing remediation instructions, and SHALL not retry the retrieval automatically.
5. IF an authentication failure or session expiry occurs during a publication attempt, THEN THE Orchestrator SHALL set the Day_State to FAILED, SHALL trigger a failure notification containing remediation instructions, and SHALL not retry the authentication failure automatically.
6. THE Engine SHALL fail an execution safely rather than attempt to bypass authentication, MFA, CAPTCHA, bot detection, rate limits, or other access controls.
7. THE Engine SHALL grant the Orchestrator least-privilege IAM permissions scoped separately for DynamoDB access, Bedrock invocation, Secret_Store read, SNS publish, and CloudWatch logging.

### Requirement 9: Idempotency

**User Story:** As an AWS builder, I want executions to be idempotent, so that repeated triggers never publish the same day twice.

#### Acceptance Criteria

1. WHEN an execution targets a Campaign_Day, THE Orchestrator SHALL derive a deterministic Idempotency_Key from the campaign_id and day such that identical campaign_id and day inputs always produce the same Idempotency_Key and any difference in campaign_id or day produces a different Idempotency_Key.
2. WHEN the Orchestrator attempts to record a PUBLISHED Day_State, THE Orchestrator SHALL use a conditional DynamoDB write that succeeds only if the Campaign_Day is not already in the PUBLISHED Day_State.
3. IF a repeated Scheduler invocation or manual run targets a Campaign_Day whose Day_State is already PUBLISHED, THEN THE Orchestrator SHALL not perform any publication action for that Campaign_Day and SHALL record exactly one prevented-duplicate entry for that execution while preserving the existing PUBLISHED Day_State unchanged.
4. IF a conditional DynamoDB write to mark a Campaign_Day PUBLISHED fails because the Campaign_Day is already PUBLISHED, THEN THE Orchestrator SHALL treat the execution as a prevented duplicate, SHALL not retry the write, and SHALL end the execution within 5 seconds of the write failure without performing any additional publication action.
5. IF a conditional DynamoDB write to mark a Campaign_Day PUBLISHED fails for a reason other than the Campaign_Day already being PUBLISHED, THEN THE Orchestrator SHALL not record the Campaign_Day as PUBLISHED, SHALL retry the write up to 3 times with the same Idempotency_Key, and SHALL emit an error indication identifying the failed Campaign_Day if all attempts fail.

### Requirement 10: Notifications

**User Story:** As an AWS builder, I want SNS notifications for outcomes, so that I know what happened without checking manually.

#### Acceptance Criteria

1. WHEN a Campaign_Day transitions to PUBLISHED, THE Notifier SHALL publish a success notification through Amazon SNS containing the campaign identifier, day identifier, article title, status value PUBLISHED, an ISO 8601 UTC timestamp, and the Builder_Center URL when available.
2. IF a Campaign_Day transitions to PUBLISHED but the Builder_Center URL is not available, THEN THE Notifier SHALL publish the success notification with the Builder_Center URL field set to a value indicating the URL is unavailable and SHALL publish all other required fields.
3. WHEN a Campaign_Day transitions to FAILED, THE Notifier SHALL publish a failure notification through Amazon SNS containing the campaign identifier, day identifier, stage identifier, status value FAILED, a sanitized error reason of at most 1024 characters, and a remediation action.
4. THE Notifier SHALL exclude passwords, cookies, access tokens, session headers, and personal authentication details from every notification field, including the sanitized error reason.
5. IF publishing a notification through Amazon SNS fails, THEN THE Notifier SHALL retry publishing up to 3 times, and THE Engine SHALL not change an already-successful publication outcome to FAILED.
6. WHERE SMS notification is configured, THE Notifier SHALL publish the notification through the configured SMS channel in addition to email within 60 seconds of the triggering transition.

### Requirement 11: Manual and Scheduled Execution

**User Story:** As an AWS builder, I want scheduled daily runs and an authenticated manual trigger, so that the campaign runs automatically and on demand.

#### Acceptance Criteria

1. WHEN the configured execution time is reached for a campaign in Campaign_State READY or RUNNING, THE Scheduler SHALL trigger the Orchestrator to execute the next eligible Campaign_Day within 60 seconds of the configured execution time.
2. THE Scheduler SHALL evaluate the configured execution time using the campaign timezone.
3. IF the configured execution time is reached for a campaign in a Campaign_State other than READY or RUNNING, THEN THE Scheduler SHALL NOT trigger the Orchestrator and SHALL record the skipped trigger.
4. WHEN an authenticated manual run request is received for a campaign in Campaign_State READY or RUNNING, THE Command_API SHALL invoke the Orchestrator to execute the next eligible Campaign_Day.
5. IF an authenticated manual run request is received for a campaign that has no eligible Campaign_Day, THEN THE Command_API SHALL reject the request and return a response indicating that no eligible Campaign_Day exists, without invoking the Orchestrator.
6. THE Command_API SHALL require authentication for every request.
7. IF an unauthenticated request to trigger an execution is received, THEN THE Command_API SHALL reject the request and return a response indicating authentication is required, without invoking any downstream component.
8. WHEN a request is received by the Command_API, THE Command_API SHALL validate the request payload before invoking any downstream component.
9. IF the request payload fails validation, THEN THE Command_API SHALL reject the request and return a response indicating the validation failure, without invoking any downstream component and without modifying campaign state.

### Requirement 12: Retry Handling

**User Story:** As an AWS builder, I want bounded retries that avoid duplicate publication, so that transient failures recover safely.

#### Acceptance Criteria

1. WHEN a retry request is received for a Campaign_Day in Day_State FAILED, THE Orchestrator SHALL re-execute the workflow for that Campaign_Day without creating a duplicate article.
2. WHEN a publication attempt fails with a transient error, THE Orchestrator SHALL retry the publication using exponential backoff starting at 1 second and doubling each attempt, up to a maximum of 5 attempts.
3. IF a publication attempt returns an ambiguous result where the success or failure of publication cannot be determined, THEN THE Orchestrator SHALL query the publication target to determine whether the article was already published before performing any additional publication attempt.
4. IF the query in an ambiguous result verification confirms the article was already published, THEN THE Orchestrator SHALL treat the publication as successful and SHALL NOT perform additional publication attempts.
5. IF the attempt count for a Campaign_Day reaches 5 without a successful publication, THEN THE Orchestrator SHALL set the Day_State to FAILED and SHALL trigger a failure notification indicating the maximum retry count was reached.
6. WHEN a publication attempt fails due to an authentication failure, THE Orchestrator SHALL NOT retry automatically and SHALL trigger a failure notification indicating an authentication failure.
7. WHEN an attempt is made for a Campaign_Day, THE Orchestrator SHALL increment the attempt count recorded for that Campaign_Day by 1 before executing the attempt.

### Requirement 13: Campaign Status

**User Story:** As an AWS builder, I want to view campaign status, so that I can track progress and outcomes.

#### Acceptance Criteria

1. WHEN an authenticated status request is received for an existing campaign, THE Command_API SHALL return within 5 seconds the current day, count of completed days, count of failed days, next scheduled execution timestamp, last execution timestamp, publication URLs, and Campaign_State.
2. WHEN an authenticated status request is received for an existing campaign that has no completed days, no scheduled execution, no prior execution, or no publications, THE Command_API SHALL return each corresponding field as empty or null rather than omitting it.
3. IF a status request references a campaign that does not exist, THEN THE Command_API SHALL return an error indicating the campaign was not found and SHALL not create or modify any campaign data.
4. IF a status request is received that is not authenticated, THEN THE Command_API SHALL reject the request, return an error indicating authentication is required, and SHALL not return any Campaign_State data.

### Requirement 14: Pause and Resume

**User Story:** As an AWS builder, I want to pause and resume a campaign, so that I can stop and continue execution on my terms.

#### Acceptance Criteria

1. WHEN an authenticated pause request is received for a campaign in Campaign_State RUNNING or READY, THE Campaign_Manager SHALL set the Campaign_State to PAUSED within 5 seconds and return the resulting Campaign_State.
2. WHILE a campaign is in Campaign_State PAUSED, THE Orchestrator SHALL not execute any Campaign_Day for that campaign.
3. WHEN an authenticated resume request is received for a campaign in Campaign_State PAUSED, THE Campaign_Manager SHALL set the Campaign_State to RUNNING within 5 seconds and return the resulting Campaign_State.
4. IF a pause request is received for a campaign that is not in Campaign_State RUNNING or READY, THEN THE Campaign_Manager SHALL reject the request, leave the Campaign_State unchanged, and return an error identifying the current Campaign_State.
5. IF a resume request is received for a campaign that is not in Campaign_State PAUSED, THEN THE Campaign_Manager SHALL reject the request, leave the Campaign_State unchanged, and return an error identifying the current Campaign_State.
6. IF a pause or resume request references a campaign that does not exist or is not authenticated, THEN THE Campaign_Manager SHALL reject the request, leave all campaign data unchanged, and return an error indicating the campaign was not found or authentication is required.

### Requirement 15: Campaign Completion and Reusability

**User Story:** As an AWS builder, I want campaigns to complete cleanly and the engine to be reusable for any length, so that I can run additional campaigns of any duration.

#### Acceptance Criteria

1. WHEN a Campaign_Day whose day number equals the campaign total day count transitions to PUBLISHED, AND the transition completes within 5 seconds, THE Campaign_Manager SHALL set the Campaign_State to COMPLETED, provided the campaign total day count is a valid value between 1 and 365 inclusive.
2. IF a request to publish an additional Campaign_Day is received WHILE that campaign is in Campaign_State COMPLETED, THEN THE Orchestrator SHALL reject the request, return an error indicating the campaign is already COMPLETED, and preserve the existing Campaign_State and all published Campaign_Day records unchanged.
3. THE Engine SHALL determine campaign completion by comparing the published day number against the campaign total day count read from the campaign configuration, without using a hard-coded day count value.
4. THE Engine SHALL support campaigns with any configured total day count between 1 and 365 inclusive without using hard-coded topic values.
5. IF a campaign is configured with a total day count outside the range 1 to 365 inclusive, THEN THE Campaign_Manager SHALL reject the campaign configuration, return an error indicating the day count is out of range, and preserve the campaign in its prior state without initiating completion.
6. WHEN a new campaign creation request is received after another campaign is COMPLETED, THE Campaign_Manager SHALL create the new campaign using the same Engine.

### Requirement 16: Campaign and Day State Transitions

**User Story:** As an AWS builder, I want invalid state transitions rejected, so that campaign and day states remain consistent.

#### Acceptance Criteria

1. THE Campaign_Manager SHALL permit Campaign_State transitions only along the sequence DRAFT to APPROVED, APPROVED to READY, READY to RUNNING, RUNNING to PAUSED, PAUSED to RUNNING, and RUNNING to COMPLETED.
2. IF a Campaign_State transition that is not permitted is requested, THEN THE Campaign_Manager SHALL reject the transition, return an error identifying the current and requested Campaign_State, and preserve the current Campaign_State unchanged.
3. THE Orchestrator SHALL permit Day_State transitions only along the sequence PLANNED to GENERATING, GENERATING to GENERATED, GENERATED to READY, READY to PUBLISHING, PUBLISHING to PUBLISHED, and any of GENERATING, GENERATED, READY, or PUBLISHING to FAILED.
4. IF a Day_State transition that is not permitted is requested, THEN THE Orchestrator SHALL reject the transition, return an error identifying the current and requested Day_State, and preserve the current Day_State unchanged.
5. WHEN a Campaign_Day in Day_State FAILED is retried, THE Orchestrator SHALL transition the Day_State to GENERATING.
6. IF a retry is requested for a Campaign_Day whose Day_State is not FAILED, THEN THE Orchestrator SHALL reject the retry, return an error indicating retry is permitted only from Day_State FAILED, and preserve the current Day_State unchanged.

### Requirement 17: Observability

**User Story:** As an AWS builder, I want structured logs and metrics, so that I can trace executions and monitor outcomes without exposing secrets.

#### Acceptance Criteria

1. WHEN an execution begins, THE Orchestrator SHALL attach a Correlation_ID to all structured logs and metrics emitted during that execution, where the Correlation_ID is unique per execution and persists across all components invoked within that execution.
2. THE Orchestrator SHALL emit structured CloudWatch logs recording execution duration in milliseconds, generation outcome as success or failure, publication outcome as success or failure, retry count as an integer from 0 to the configured maximum, authentication failure count as a non-negative integer, and campaign completion status as complete or incomplete.
3. THE Orchestrator SHALL emit CloudWatch metrics for ArticlesGenerated, ArticlesPublished, GenerationFailures, PublicationFailures, AuthenticationFailures, DuplicateRunsPrevented, and CampaignsCompleted, each as a non-negative integer count.
4. THE Engine SHALL exclude passwords, cookies, access tokens, session headers, and personal authentication details from all logs and metrics.
5. IF a log or metric payload is found to contain a value from an excluded category, THEN THE Engine SHALL redact the value before emission and record a redaction event indicating that a sensitive value was removed, without including the value itself.

### Requirement 18: Cost Constraints

**User Story:** As an AWS builder, I want the automation to stay inexpensive, so that a campaign costs less than USD 5.

#### Acceptance Criteria

1. THE Engine SHALL use serverless, event-triggered components and SHALL NOT deploy infrastructure that runs continuously between scheduled executions for executing campaigns.
2. THE Engine SHALL use DynamoDB on-demand capacity for campaign and day storage.
3. THE Engine SHALL use the Scheduler to trigger execution rather than keeping a Lambda function running continuously.
4. THE Engine SHALL produce a cost estimate before deployment that projects total cost strictly below USD 5.00 for a 21-day campaign.
5. IF the pre-deployment cost estimate projects a total cost equal to or greater than USD 5.00 for a 21-day campaign, THEN THE Engine SHALL fail deployment and identify the projected total cost.

### Requirement 19: Configuration and Infrastructure as Code

**User Story:** As an AWS builder, I want the system defined as configurable infrastructure as code, so that I can deploy and reconfigure it without secrets in source control.

#### Acceptance Criteria

1. THE Engine SHALL be defined as infrastructure as code using Terraform.
2. THE Engine SHALL expose configuration for campaign identifier, notification email, notification phone number, schedule, timezone, Bedrock model, AWS region, notification topic, and publishing configuration.
3. THE Engine SHALL exclude all secrets from source control.
4. WHERE a configuration value is required but not provided, THE Engine SHALL fail deployment and identify each missing required configuration value by name.
5. IF a provided configuration value fails format validation for its field, THEN THE Engine SHALL fail deployment and identify each invalid configuration value by name along with the expected format.

### Requirement 20: Article Structure and Authenticity

**User Story:** As an AWS builder, I want generated articles to follow a consistent authentic structure, so that the series reads as a genuine learning journey.

#### Acceptance Criteria

1. THE Content_Generator SHALL request article bodies that use portable fenced text diagrams rather than diagram syntaxes whose rendering behavior in Builder_Center is uncertain.
2. THE Content_Generator SHALL request content in a personal, practical, beginner-friendly voice that documents learning rather than asserting unquestionable authority.
3. IF generated content asserts employment history, projects, production incidents, certifications, customer environments, or performance measurements, THEN THE Content_Validator SHALL reject the article and identify each instance of fabricated content by its asserted claim.
4. IF generated content contains a diagram expressed in a syntax other than a portable fenced text diagram, THEN THE Content_Validator SHALL reject the article and identify each non-portable diagram.
