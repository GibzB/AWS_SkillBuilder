# Implementation Plan: Dice Roll API

## Overview

Build the serverless Dice Roll API end to end in the conventional SAM layout. The work proceeds in incremental, verifiable steps: lay down the project structure, implement the pure Lambda handler, declare the SAM infrastructure, stand up a local test project that exercises the handler via property-based and example tests, verify locally, then validate/build/deploy to AWS and smoke-test the live endpoint. Each step builds on the previous one so there is no orphaned code: the handler is written before it is tested, the template references the handler, and the test project imports the handler directly.

The handler is a pure function of its `event` input (deterministic except for `Math.random()`), so its contract is covered by the 5 correctness properties from the design plus a few example/edge tests. Infrastructure and deployment behavior are verified by `sam validate`, `sam build`, and live `curl` smoke checks rather than property tests.

All AWS-interacting commands (`sam deploy`, `sam delete`, any `aws` CLI call) MUST include `--profile K1-Kitstek-Billy`. Local-only commands (`sam validate`, `sam build`, `node --test`) do not require the profile.

Implementation language: JavaScript (Node.js 24.x, ESM) — as specified in the design document.

## Tasks

- [x] 1. Create the project directory structure
  - Create the `dice-roll-api/` project root inside the workspace
  - Create the `dice-roll-api/lambda/` directory for the handler source
  - Create the `dice-roll-api/tests/` directory for local tests
  - This establishes the conventional SAM layout so the CLI can locate the function code and template, and keeps test code out of the deployment artifact
  - _Requirements: 6.1, 6.2_

- [x] 2. Implement the Lambda handler
  - [x] 2.1 Write `dice-roll-api/lambda/index.mjs`
    - Export an asynchronous `handler` function as an ESM named export that accepts a single API Gateway `event` object (`export const handler = async (event) => { ... }`)
    - Read `event.queryStringParameters`, defaulting to `{}` when absent or `null` so no unhandled error occurs
    - Resolve `const sides = parseInt(params.sides) || 6` so fractional strings truncate toward zero and absent/empty/non-numeric/`"0"` values fall back to 6
    - If `sides < 2 || sides > 100`, return an API Gateway proxy response with `statusCode: 400`, `headers: { "Content-Type": "application/json" }`, and `body: JSON.stringify({ error: "Sides must be between 2 and 100." })`
    - Otherwise compute `const result = Math.floor(Math.random() * sides) + 1` and return `statusCode: 200`, `headers: { "Content-Type": "application/json" }`, and `body: JSON.stringify({ sides: sides, roll: result })`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 6.3, 6.4, 6.5_

- [x] 3. Declare the SAM infrastructure
  - [x] 3.1 Write `dice-roll-api/template.yaml`
    - Set `AWSTemplateFormatVersion: '2010-09-09'` and `Transform: AWS::Serverless-2016-10-31`
    - Define resource `DiceRollFunction` of type `AWS::Serverless::Function` with `Handler: index.handler`, `Runtime: nodejs24.x`, `CodeUri: lambda/`, `Timeout: 5`, and `MemorySize: 128`
    - Add an `Api` event (e.g. `RollDice`) with `Path: /roll` and `Method: get` so SAM implicitly creates the REST API, `/roll` resource, and `Prod` stage
    - Add an `Outputs` section exposing the endpoint as a fully qualified HTTPS URL: `!Sub "https://${ServerlessRestApi}.execute-api.${AWS::Region}.amazonaws.com/Prod/roll"`
    - _Requirements: 4.1, 4.2, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.2_

- [x] 4. Set up the local test project
  - [x] 4.1 Create `dice-roll-api/package.json`
    - Add `"type": "module"` so `.mjs`/ESM imports resolve, a `"test"` script that runs a single execution (e.g. `node --test`), and `fast-check` as a dev dependency
    - Install dependencies so `fast-check` is available to the test files
    - _Requirements: 6.3_

- [x] 5. Implement property-based tests for the handler
  - [x] 5.1 Write the test file scaffold `dice-roll-api/tests/handler.test.mjs`
    - Import `fast-check`, `node:test`, `node:assert/strict`, and the `handler` from `../lambda/index.mjs`
    - This file hosts the property and example tests added in the following sub-tasks
    - _Requirements: 6.3_

  - [x]* 5.2 Write property test for valid sides producing a bounded 200 roll
    - **Property 1: Valid sides produce a bounded roll in a well-formed 200 response**
    - Generate `fc.integer({ min: 2, max: 100 })`, pass as `String(n)`; assert `statusCode` 200, `Content-Type` `application/json`, body `sides === n`, and integer `roll` with `1 <= roll <= n`
    - Minimum 100 iterations (`{ numRuns: 100 }`); tag: `// Feature: dice-roll-api, Property 1: Valid sides produce a bounded roll in a well-formed 200 response`
    - **Validates: Requirements 1.1, 1.4, 1.5, 3.1**

  - [x]* 5.3 Write property test for falsy/unparseable sides defaulting to a six-sided roll
    - **Property 2: Falsy or unparseable sides defaults to a six-sided successful roll**
    - Generate `fc.oneof` of: missing `sides`, `""`, non-numeric strings, `"0"`, and events with absent/`null` `queryStringParameters`; assert `statusCode` 200, body `sides === 6`, integer `roll` with `1 <= roll <= 6`, and no `error` field
    - Minimum 100 iterations; tag: `// Feature: dice-roll-api, Property 2: Falsy or unparseable sides defaults to a six-sided successful roll`
    - **Validates: Requirements 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 6.5**

  - [x]* 5.4 Write property test for fractional sides strings truncating toward zero
    - **Property 3: Fractional sides strings truncate toward zero**
    - Compose `fc.integer({ min: 2, max: 100 })` with `fc.integer({ min: 0, max: 999 })` into a `"n.fraction"` string; assert the returned body `sides` equals the truncated integer part `n`
    - Minimum 100 iterations; tag: `// Feature: dice-roll-api, Property 3: Fractional sides strings truncate toward zero`
    - **Validates: Requirements 1.3**

  - [x]* 5.5 Write property test for out-of-range sides producing the standard 400 error
    - **Property 4: Out-of-range sides produce the standard 400 error**
    - Generate `fc.oneof(fc.constant(1), fc.integer({ min: -1000, max: 0 }).filter(truthy), fc.integer({ min: 101, max: 10000 }))`, pass as string; assert `statusCode` 400, `Content-Type` `application/json`, and body `error === "Sides must be between 2 and 100."`
    - Minimum 100 iterations; tag: `// Feature: dice-roll-api, Property 4: Out-of-range sides produce the standard 400 error`
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5**

  - [x]* 5.6 Write property test for every response body being valid JSON
    - **Property 5: Every response body is valid JSON**
    - Generate `fc.oneof` spanning the valid, defaulting, and out-of-range branches; assert `JSON.parse(res.body)` succeeds and `headers["Content-Type"]` is `application/json` for every input
    - Minimum 100 iterations; tag: `// Feature: dice-roll-api, Property 5: Every response body is valid JSON`
    - **Validates: Requirements 1.4, 1.5, 3.4, 3.5**

- [x] 6. Implement example/edge unit tests for the handler
  - [x]* 6.1 Write example and contract unit tests in `dice-roll-api/tests/handler.test.mjs`
    - `sides=0` resolves to 6 and returns a 200 roll (explicit edge of the `|| 6` fallback)
    - Event with `queryStringParameters: null` returns a 200 default roll without throwing
    - The module exports `handler` as a function whose invocation returns a Promise (async ESM named-export contract)
    - _Requirements: 2.4, 6.3, 6.5_

- [x] 7. Verify the handler locally
  - [x] 7.1 Run the local test suite
    - Run the `test` script (single execution, e.g. `node --test`) from `dice-roll-api/` and confirm all property and example tests pass
    - Fix the handler if any test fails, then re-run until green
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 6.3, 6.4, 6.5_

- [x] 8. Checkpoint - handler and local tests
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Validate and build with the SAM CLI
  - [x] 9.1 Run `sam validate` and `sam build`
    - From `dice-roll-api/`, run `sam validate` and confirm it reports a success result (non-zero exit means a template error to fix). This is local-only; the profile is optional.
    - Run `sam build` and confirm it packages the function into the `.aws-sam/` artifact and reports build success. This is local-only; the profile is optional.
    - _Requirements: 5.7, 7.1, 7.2, 7.3, 7.4, 8.3_

- [x] 10. Deploy to AWS (requires live AWS access, profile K1-Kitstek-Billy)
  - [x] 10.1 Deploy the stack with the SAM CLI
    - From `dice-roll-api/`, run `sam deploy --guided --profile K1-Kitstek-Billy` and complete the guided prompts (writes `samconfig.toml`)
    - Confirm the deploy creates all resources and prints the API Gateway endpoint URL as a fully qualified HTTPS URL in the output; capture that URL for smoke testing
    - MUST include `--profile K1-Kitstek-Billy` because this command interacts with AWS
    - _Requirements: 4.1, 4.2, 4.5, 5.6, 7.5, 7.6, 7.7, 8.1_

- [x] 11. Smoke-test the live endpoint (requires live AWS access, profile K1-Kitstek-Billy)
  - [x] 11.1 Curl the deployed endpoint and assert responses
    - `curl` the reported `/Prod/roll` URL with `?sides=12` and confirm HTTP 200 with JSON `sides: 12` and `roll` in `[1, 12]`
    - `curl` the URL with no query params and confirm HTTP 200 with JSON `sides: 6` and `roll` in `[1, 6]`
    - `curl` the URL with `?sides=1` and confirm HTTP 400 with JSON `error: "Sides must be between 2 and 100."`
    - Any `aws` CLI call used to inspect resources during this step MUST include `--profile K1-Kitstek-Billy`
    - _Requirements: 1.1, 1.2, 1.5, 2.1, 3.2, 3.5, 4.2, 4.5, 7.7, 8.2_

- [x] 12. Final checkpoint - deployed and verified
  - Ensure the live endpoint responds correctly for all three smoke cases, ask the user if questions arise.

- [x] 13. (Optional) Tear down deployed resources (requires live AWS access, profile K1-Kitstek-Billy)
  - [x]* 13.1 Delete the stack with the SAM CLI
    - From `dice-roll-api/`, run `sam delete --profile K1-Kitstek-Billy` to remove the CloudFormation stack, the Lambda function, the API Gateway endpoint, and all associated resources, returning to a clean slate and avoiding ongoing charges
    - MUST include `--profile K1-Kitstek-Billy` because this command interacts with AWS
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP; they include the property tests (5.2-5.6), the example/edge unit tests (6.1), and the teardown task (13.1).
- Core implementation tasks (handler, template, test project setup, local verification, validate/build, deploy, smoke test) are never marked optional.
- Tasks 10, 11, and 13 require live AWS access and MUST authenticate with `--profile K1-Kitstek-Billy`. Local-only commands (`sam validate`, `sam build`, `node --test`) do not require the profile.
- Each property test is its own sub-task, implements exactly one correctness property from the design, runs a minimum of 100 iterations, and carries the `// Feature: dice-roll-api, Property N: ...` tag.
- Checkpoints ensure incremental validation: once after local handler tests pass, once after the live endpoint is verified.
- Each task references specific requirements (and properties where applicable) for traceability.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1"] },
    { "id": 2, "tasks": ["5.1"] },
    { "id": 3, "tasks": ["5.2"] },
    { "id": 4, "tasks": ["5.3"] },
    { "id": 5, "tasks": ["5.4"] },
    { "id": 6, "tasks": ["5.5"] },
    { "id": 7, "tasks": ["5.6"] },
    { "id": 8, "tasks": ["6.1"] },
    { "id": 9, "tasks": ["7.1"] },
    { "id": 10, "tasks": ["9.1"] },
    { "id": 11, "tasks": ["10.1"] },
    { "id": 12, "tasks": ["11.1"] },
    { "id": 13, "tasks": ["13.1"] }
  ]
}
```
