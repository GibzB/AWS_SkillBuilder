# AWS Builder Learning Automation Engine

A low-cost, serverless, AWS-native engine that turns an approved AWS learning
topic into a first-person Builder Center article every campaign day, validates
it deterministically, publishes it through an isolated publishing adapter,
records the outcome, and notifies you through Amazon SNS. The engine is reusable
for any campaign length between 1 and 365 days.

> **Publishing status.** There is no confirmed official or permitted programmatic
> AWS Builder Center publishing API (discovery, Feb-Mar 2026). The deployable MVP
> ships with a **MockPublisher** so the full pipeline runs end to end and stays
> under budget. The real `BuilderCenterPublisher` is a documented, blocked
> discovery task and is never selected implicitly. See
> [Builder Center publishing](#builder-center-publishing).

---

## Architecture

```text
                    +----------------------+
                    | EventBridge Scheduler|
                    +----------+-----------+
                               | (timezone-aware, READY/RUNNING only)
                               v
                    +----------------------+      Command API (Lambda
                    |  Orchestrator Lambda |<---- Function URL, IAM/SigV4 auth)
                    +----------+-----------+
                               |
        +----------------+-----+-----+-----------------+
        v                v           v                 v
  +-----------+   +-----------+  +-----------+   +-------------+
  | DynamoDB  |   |  Bedrock  |  | Validator |   |  Publisher  |
  | on-demand |   | small mdl |  | (pure)    |   | Mock (MVP)  |
  +-----------+   +-----------+  +-----------+   +------+------+
                                                        |
                                                        v
                                                  +-----------+
                                                  |    SNS    |--> Email / SMS
                                                  +-----------+
```

- **Orchestrator** coordinates one campaign-day run and delegates every rule to
  small, dependency-injected modules. It contains no business logic itself.
- **Campaign store** is a single DynamoDB table (on-demand) using conditional
  writes for the state machine and idempotency.
- **Content generator** calls a small Bedrock model (default Amazon Nova Micro)
  with a versioned prompt and parses a bounded structured article.
- **Content validator** runs deterministic quality gates before publication.
- **Publishing adapter** isolates Builder Center specifics behind
  `publish_article(article) -> PublicationResult`.
- **Notifier** publishes success/failure notifications through SNS with secret
  redaction and failure isolation.

State machines, idempotency, and completion are documented in
`.kiro/specs/aws-builder-automation/design.md`.

## Prerequisites

- Python 3.12
- Terraform >= 1.5
- An AWS account with access to DynamoDB, Lambda, EventBridge Scheduler, SNS,
  Bedrock (the configured model in your region), Secrets Manager, and CloudWatch
- AWS credentials configured locally (for example via `aws configure` or SSO)

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

All configuration is Terraform variables; **no secrets live in source control**.
Copy the example and edit a working copy:

```bash
cp config/example.tfvars config/my-campaign.tfvars
# edit config/my-campaign.tfvars
```

| Variable | Meaning | Default |
| --- | --- | --- |
| `campaign_id` | Stable identifier used to name/correlate resources | (required) |
| `notification_email` | Email subscribed to the SNS topic | (required) |
| `notification_phone` | Optional E.164 number for SMS; empty disables SMS | `""` |
| `schedule_time` | Daily execution time `HH:MM`, in the campaign timezone | `09:00` |
| `timezone` | IANA timezone for scheduling | `Africa/Nairobi` |
| `start_date` | Campaign start date `YYYY-MM-DD` | (required) |
| `total_days` | Campaign length (1-365) | `21` |
| `bedrock_model_id` | Bedrock model **or inference-profile** ID for generation | `eu.amazon.nova-micro-v1:0` |
| `bedrock_foundation_model_id` | Underlying foundation-model ID the profile routes to (IAM scoping) | `amazon.nova-micro-v1:0` |
| `bedrock_inference_profile_regions` | Regions the inference profile may route to | EU Nova regions |
| `aws_region` | Deployment region | `eu-west-1` |
| `publisher_selection` | `mock` or `builder_center` | `mock` |
| `session_secret_arn` | Secrets Manager ARN for session material (builder_center only) | `""` |

The Builder ID password is never stored in source, environment variables, logs,
DynamoDB, or plaintext config. Session material (if a real publisher is ever
enabled) lives only in Secrets Manager and is resolved at runtime.

## Cost

Target is **under USD 5** for a 21-day campaign on the MockPublisher / API-only
path. A pre-deploy gate fails if the projection reaches USD 5.00:

```bash
python -m app.common.cost_estimate --days 21 --publisher mock
# Projected 21-day cost: USD 0.22 (ceiling USD 5.00)
# OK: projected cost is under budget.
```

Dominant costs are Bedrock generation and DynamoDB, both small at this volume.
Enabling `builder_center` with browser automation would change this profile and
requires an explicit choice.

## Deployment

First build the Lambda deployment package, then plan/apply:

```bash
# 1. Stage the Lambda package (app/ + runtime deps, excluding boto3).
scripts/build_lambda.sh

# 2. Deploy.
cd infra
terraform init
terraform plan  -var-file=../config/my-campaign.tfvars
terraform apply -var-file=../config/my-campaign.tfvars
```

`scripts/build_lambda.sh` stages `infra/build/lambda_src/` (the `app/` package
plus `markdown-it-py`; boto3/botocore are omitted because the Lambda runtime
provides them). Terraform zips that directory into the deployment artifact and
redeploys only when its contents change. A Terraform precondition fails with a
clear message if you run plan/apply without building first. Re-run the build
after changing application code.

Terraform provisions the DynamoDB table (on-demand), the Orchestrator Lambda
(handler `app.orchestrator.handler.lambda_handler`), a Function URL with
`AuthType = AWS_IAM`, the EventBridge Scheduler daily schedule, the SNS topic
and email subscription, least-privilege IAM scoped per service, and the
CloudWatch log group. Confirm the SNS email subscription from your inbox after
the first apply.

## Manual run and campaign operations

The Command API is an IAM-authenticated Lambda Function URL. Requests are
SigV4-signed; state-changing calls require a unique `nonce` and a fresh
`timestamp`. Actions:

```text
create   -> create a campaign (DRAFT)
approve  -> approve topics (DRAFT -> APPROVED -> READY)
run      -> run the next eligible day now
retry    -> re-run a FAILED day (no duplicate)
status   -> campaign progress and publication URLs
pause    -> pause a RUNNING/READY campaign
resume   -> resume a PAUSED campaign
```

Example request body (signed with SigV4 against the Function URL):

```json
{
  "action": "run",
  "campaign_id": "aws-learning-21",
  "nonce": "unique-per-request",
  "timestamp": "2026-01-01T09:00:00+00:00"
}
```

Unauthenticated requests are rejected before any downstream effect, and there is
no unauthenticated RUN endpoint.

## Scheduler

EventBridge Scheduler invokes the Orchestrator daily at `schedule_time` in the
campaign timezone. A trigger runs the next eligible day only when the campaign
is `READY` or `RUNNING`; any other state (including `PAUSED`) is skipped and
recorded. Nothing runs between executions.

## Testing

```bash
python -m pytest -q                 # all tests
python -m pytest -q -m property     # property-based tests (Hypothesis)
python -m pytest -q -m unit         # unit tests
python -m pytest -q -m integration  # in-memory end-to-end (MockPublisher)
python -m mypy                      # strict type checking
```

Publishing-path tests use the MockPublisher and never contact Builder Center.
The `integration` tests here use in-memory fakes for DynamoDB and SNS; tests
against real/emulated AWS are out of scope for the MVP.

## Builder Center publishing

The publishing mechanism is the highest-risk component and is isolated behind a
single interface. The evaluation order for a real publisher is: an official or
supported API if one exists, then a supported authenticated integration, then
controlled browser automation only if permitted by Builder Center terms and
proven reliable. As of the discovery window no safe, permitted, reliable
mechanism was confirmed, so:

- the MVP runs on `MockPublisher` (`publisher_selection = mock`);
- `BuilderCenterPublisher` fails safe, never bypasses MFA/CAPTCHA/bot
  protection/rate limits, and is never auto-selected;
- enabling a real publisher requires a one-time manual Builder ID sign-in /
  session capture, confirmation that automation is permitted, and an explicit
  configuration choice.

## News campaign (aws-news-30)

A second campaign, `aws-news-30`, turns the AWS What's New feed into 30 daily
articles and a Sunday roundup. It runs as its own stack with its own Terraform
workspace, so `aws-learning-21` is not touched.

**How a day is chosen.** On the day, the Lambda reads the feed
(`https://aws.amazon.com/about-aws/whats-new/recent/feed/`) and picks one item.
About 65% of picks favour services covered by the earlier published articles.
Regional-availability announcements are never a daily topic; they only appear in
the weekly roundup. Each article gives a summary in the builder's own words, what
the feature is for and its value, a few use cases, a hands-on section, an
architecture diagram when one helps, and the announcement link on the last line.
When a command is too new to verify against the installed AWS CLI, the article
carries a `> **Validate before you run:**` note above the command.

**Schedules.** The daily run uses `schedule_time`. The weekly roundup runs on
Sundays at `weekly_roundup_time` (default 18:00, campaign timezone) and only
exists when `weekly_roundup_enabled = true`; it defaults to false, so existing
stacks plan as a no-op.

**Commands.**

```bash
# Deploy (plan first; apply only after reviewing the plan)
cp config/example-news.tfvars config/news-campaign.tfvars
scripts/deploy.sh default --tfvars config/news-campaign.tfvars \
  --workspace-suffix aws-news-30 --email you@your-domain --plan-only

# Create and approve the campaign (generates nothing; use --through N to generate N days now)
python tools/bootstrap_campaign.py --news --email you@your-domain

# Run the roundup once, for the current week or a given one
python tools/bootstrap_campaign.py --roundup --week 1

# Draft in Builder Center (daily, then weekly)
scripts/run.sh publish --news --from N --to N --auto-tags
scripts/run.sh publish --news --week N --auto-tags

# Track progress; W rows never change the daily streak
scripts/run.sh progress --doc docs/news-campaign-progress.md --day N --state published
scripts/run.sh progress --doc docs/news-campaign-progress.md --week N --state published
```

`scripts/deploy.sh` without `--tfvars` and `--workspace-suffix` behaves as
before (config `config/my-campaign.tfvars`, workspace `acct-<account id>`).

**Diagrams.** The Lambda stores a small JSON diagram spec with the article.
`tools/diagrams/render.py` turns it into a draw.io file with AWS icons and
exports a PNG, and `publish --news` uploads the PNG through the editor's Image
button. PNG export needs the draw.io desktop CLI (`brew install --cask drawio`);
without it only the `.drawio` file is written, and you can open it with the
draw.io MCP in an interactive session. The S3 copy of an article shows a broken
image at the diagram marker because the PNG is only uploaded to Builder Center.

**Publish stays manual.** The tools fill the editor and upload the diagram.
Clicking Publish, and every like, comment, reply and vote, is always yours.

## Security

- Least-privilege IAM, scoped separately for DynamoDB, Bedrock, Secrets Manager,
  SNS, and CloudWatch.
- Secrets only in Secrets Manager, encrypted at rest, resolved at runtime.
- Structured logs and metrics with per-execution correlation IDs and automatic
  redaction of passwords, cookies, tokens, session headers, and auth details.
- IAM-authenticated command surface with payload validation and replay
  protection.

## Troubleshooting

- **No SNS emails:** confirm the subscription link AWS emails you after the
  first `terraform apply`.
- **Generation failures:** confirm the configured `bedrock_model_id` is
  available in your `aws_region` and that the Lambda role can invoke it.
- **"Invocation ... with on-demand throughput isn't supported":** the model
  requires an inference profile in this region (Amazon Nova does in several
  regions, including eu-west-1). Set `bedrock_model_id` to the profile ID
  (for example `eu.amazon.nova-micro-v1:0`) and keep
  `bedrock_foundation_model_id` / `bedrock_inference_profile_regions` set so
  IAM allows the profile's cross-region routing. List profiles with:
  `aws bedrock list-inference-profiles --region <region>`.
- **A day stuck FAILED:** call `retry`; it re-runs from generation without
  creating a duplicate. Check CloudWatch logs (filtered by the correlation ID)
  for the `stage` and sanitized error.
- **`run` returns "no eligible day":** the campaign is complete or has no
  PLANNED/FAILED day.

## Project layout

```text
app/            application code (campaign, generation, validation, publishing,
                notify, orchestrator, common, news, diagrams)
tools/          local tools (builder_publish, diagrams, bootstrap_campaign.py)
infra/          Terraform (DynamoDB, Lambda, Function URL, Scheduler, SNS, IAM)
config/         example.tfvars, example-news.tfvars (placeholders only)
tests/          unit, property, and integration tests
.kiro/specs/    requirements.md, design.md, tasks.md
```
