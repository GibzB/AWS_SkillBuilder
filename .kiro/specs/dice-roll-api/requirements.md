# Requirements Document

## Introduction

The Dice Roll API is a serverless web API hosted on AWS. It exposes a single public HTTP endpoint that rolls a virtual die with a caller-specified number of sides and returns the result as JSON. The system is implemented as an AWS Lambda function (Node.js 24.x) fronted by an Amazon API Gateway REST endpoint, with all cloud resources defined as Infrastructure as Code in an AWS SAM template and deployed via the AWS SAM CLI.

The endpoint accepts an optional `sides` query string parameter (defaulting to a standard 6-sided die), validates that the requested side count is within supported bounds, computes a uniformly random roll result, and returns a structured JSON response. The endpoint is intentionally public (no authentication), which is acceptable for this project. All AWS interactions performed by the SAM CLI and AWS CLI MUST use the named AWS profile `K1-Kitstek-Billy`.

## Glossary

- **Dice_Roll_API**: The complete serverless system comprising the Lambda function, the API Gateway REST endpoint, and the supporting AWS resources defined in the SAM template.
- **Dice_Roll_Function**: The AWS Lambda function (handler `index.handler`, source file `lambda/index.mjs`, runtime Node.js 24.x) that computes a dice roll and returns an API Gateway proxy response.
- **API_Gateway_Endpoint**: The Amazon API Gateway REST resource that exposes `GET /roll` and invokes the Dice_Roll_Function.
- **SAM_Template**: The `template.yaml` Infrastructure as Code file that declares all AWS resources using `Transform: AWS::Serverless-2016-10-31`.
- **SAM_CLI**: The AWS Serverless Application Model command line interface used to validate, build, deploy, and delete the Dice_Roll_API.
- **AWS_Profile**: The named AWS credentials profile `K1-Kitstek-Billy` used to authenticate all AWS CLI and SAM CLI operations.
- **Sides**: The integer number of faces on the die, provided via the `sides` query string parameter.
- **Roll**: The computed random integer result of rolling the die, an integer between 1 and Sides inclusive.
- **Query_String_Parameter**: A key/value option appended to the request URL after `?`, passed to the Dice_Roll_Function through the API Gateway event object.

## Requirements

### Requirement 1: Roll a die with a specified number of sides

**User Story:** As an API consumer, I want to request a dice roll with a chosen number of sides, so that I can obtain a random result for a die of that size.

#### Acceptance Criteria

1. WHEN a `GET /roll` request is received with a `sides` Query_String_Parameter whose parsed integer value is between 2 and 100 inclusive, THE Dice_Roll_Function SHALL set Sides to that parsed integer value and compute a Roll as an integer between 1 and Sides inclusive.
2. WHEN a `GET /roll` request is received in which the `sides` Query_String_Parameter is absent, empty, or cannot be parsed as a positive integer, THE Dice_Roll_Function SHALL set Sides to 6 and compute a Roll as an integer between 1 and 6 inclusive.
3. WHEN the `sides` Query_String_Parameter is a numeric string with a fractional part, THE Dice_Roll_Function SHALL set Sides to the integer part obtained by truncating toward zero before computing the Roll.
4. WHEN a Roll is computed, THE Dice_Roll_Function SHALL return an HTTP 200 response with header `Content-Type` set to `application/json`.
5. WHEN a Roll is computed, THE Dice_Roll_Function SHALL return a JSON response body containing the field `sides` equal to the resolved Sides value and the field `roll` equal to the computed Roll, where the value of `roll` is an integer no less than 1 and no greater than the value of `sides`.

### Requirement 2: Default to a six-sided die

**User Story:** As an API consumer, I want the API to assume a standard six-sided die when I do not specify a side count, so that I can get a roll without supplying parameters.

#### Acceptance Criteria

1. WHEN a `GET /roll` request is received without a `sides` Query_String_Parameter, THE Dice_Roll_Function SHALL set Sides to 6.
2. WHEN a `GET /roll` request is received with a `sides` Query_String_Parameter whose value is an empty string, THE Dice_Roll_Function SHALL set Sides to 6.
3. WHEN a `GET /roll` request is received with a `sides` Query_String_Parameter whose value does not parse to an integer, THE Dice_Roll_Function SHALL set Sides to 6.
4. WHEN a `GET /roll` request is received with a `sides` Query_String_Parameter whose value parses to the integer 0, THE Dice_Roll_Function SHALL set Sides to 6.
5. WHEN the Dice_Roll_Function applies the default side count of 6, THE Dice_Roll_Function SHALL process the request as a successful roll and SHALL NOT return an error indicating a missing or invalid parameter.

### Requirement 3: Validate the requested number of sides

**User Story:** As an API consumer, I want the API to reject out-of-range side counts, so that I receive a clear error instead of an invalid roll.

#### Acceptance Criteria

1. WHILE the resolved Sides value is between 2 and 100 inclusive, THE Dice_Roll_Function SHALL treat the resolved Sides value as valid and SHALL proceed to compute a Roll.
2. IF the resolved Sides value is a parsed integer less than 2, THEN THE Dice_Roll_Function SHALL return an HTTP 400 response.
3. IF the resolved Sides value is a parsed integer greater than 100, THEN THE Dice_Roll_Function SHALL return an HTTP 400 response.
4. WHEN an HTTP 400 response is returned for an out-of-range Sides value, THE Dice_Roll_Function SHALL set the response header `Content-Type` to `application/json`.
5. WHEN an HTTP 400 response is returned for an out-of-range Sides value, THE Dice_Roll_Function SHALL return a JSON response body containing the field `error` equal to the message `Sides must be between 2 and 100.`.

### Requirement 4: Expose a public GET endpoint via API Gateway

**User Story:** As an API consumer, I want a reachable public URL for the dice roller, so that I can call it from any HTTP client.

#### Acceptance Criteria

1. THE API_Gateway_Endpoint SHALL expose the path `/roll` under stage `Prod` for the HTTP `GET` method only.
2. WHEN a `GET /roll` request is received by the API_Gateway_Endpoint, THE API_Gateway_Endpoint SHALL invoke the Dice_Roll_Function and return the function response to the caller.
3. IF the Dice_Roll_Function fails to return a response or returns an error, THEN THE API_Gateway_Endpoint SHALL return an error response to the caller indicating that the request could not be fulfilled.
4. IF a request is received for the `/roll` path using any HTTP method other than `GET`, THEN THE API_Gateway_Endpoint SHALL reject the request with an error response indicating the method is not allowed.
5. WHERE no authentication is configured on the API_Gateway_Endpoint, THE Dice_Roll_API SHALL accept and process requests from any caller possessing the endpoint URL without requiring credentials.

### Requirement 5: Define infrastructure as code in a SAM template

**User Story:** As a developer, I want all AWS resources defined in a SAM template, so that the deployment is repeatable, shareable, and reversible.

#### Acceptance Criteria

1. THE SAM_Template SHALL declare `Transform: AWS::Serverless-2016-10-31`.
2. THE SAM_Template SHALL define the Dice_Roll_Function as a resource of type `AWS::Serverless::Function` with handler `index.handler`, runtime `nodejs24.x`, and code location `lambda/`.
3. THE SAM_Template SHALL configure the Dice_Roll_Function with a timeout of 5 seconds.
4. THE SAM_Template SHALL configure the Dice_Roll_Function with a memory size of 128 megabytes.
5. THE SAM_Template SHALL declare an API event on the Dice_Roll_Function for path `/roll` with method `get`.
6. THE SAM_Template SHALL define an Outputs section that exposes the API_Gateway_Endpoint URL for the `/roll` resource as a fully qualified HTTPS URL.
7. WHEN the SAM_Template is validated by the SAM CLI, IF any resource definition is syntactically invalid or references an undefined resource, THEN THE SAM_CLI SHALL reject the template with a non-zero exit status and an error indicating the offending resource.

### Requirement 6: Provide the project source structure

**User Story:** As a developer, I want the project files organized in a conventional SAM layout, so that the SAM CLI can locate the function code and template.

#### Acceptance Criteria

1. THE Dice_Roll_API SHALL include the Lambda handler source at `dice-roll-api/lambda/index.mjs`.
2. THE Dice_Roll_API SHALL include the SAM_Template at `dice-roll-api/template.yaml`.
3. THE Dice_Roll_Function source SHALL export an asynchronous `handler` function defined as an ESM named export that accepts a single API Gateway event object parameter.
4. WHEN the handler function is invoked with an API Gateway event object, THE Dice_Roll_Function SHALL read the Query_String_Parameters from the event object's `queryStringParameters` field.
5. IF the invoked event object's `queryStringParameters` field is absent or null, THEN THE Dice_Roll_Function SHALL treat the Query_String_Parameters as an empty set and SHALL NOT terminate with an unhandled error.

### Requirement 7: Build and deploy with the SAM CLI

**User Story:** As a developer, I want to validate, build, and deploy the API using the SAM CLI, so that I can publish the running endpoint to AWS.

#### Acceptance Criteria

1. WHEN the developer runs `sam validate`, THE SAM_CLI SHALL report a success result indicating the SAM_Template contains no structural or syntax errors.
2. IF `sam validate` detects a structural or syntax error in the SAM_Template, THEN THE SAM_CLI SHALL exit with a non-zero status and emit an error message identifying where the error occurs.
3. WHEN the developer runs `sam build`, THE SAM_CLI SHALL package the Dice_Roll_Function code into a deployment artifact and report a build success result.
4. IF `sam build` fails to package the Dice_Roll_Function code, THEN THE SAM_CLI SHALL exit with a non-zero status, emit an error message identifying the cause, and produce no deployment artifact.
5. WHEN the developer runs `sam deploy --guided`, THE SAM_CLI SHALL create all AWS resources declared in the SAM_Template and report the API_Gateway_Endpoint URL as a fully qualified HTTPS URL in the SAM_CLI output.
6. IF any AWS resource declared in the SAM_Template fails to create during `sam deploy --guided`, THEN THE SAM_CLI SHALL exit with a non-zero status, emit an error message identifying the failed resource, and roll back all resources created during that deployment.
7. WHEN the deployment completes successfully, THE Dice_Roll_API SHALL be reachable at the reported API_Gateway_Endpoint URL and return an HTTP 200 response to a valid GET request.

### Requirement 8: Use the designated AWS profile for all AWS interactions

**User Story:** As a developer, I want every AWS command to authenticate with the designated profile, so that resources are created in the correct AWS account.

#### Acceptance Criteria

1. WHEN a SAM CLI command that interacts with AWS (`sam deploy` or `sam delete`) is executed, THE developer SHALL include the argument `--profile K1-Kitstek-Billy`.
2. WHEN an AWS CLI command that interacts with AWS is executed, THE developer SHALL include the argument `--profile K1-Kitstek-Billy`.
3. WHERE a SAM CLI command is local-only (`sam validate` or `sam build`), THE developer MAY execute it with or without the argument `--profile K1-Kitstek-Billy`, producing identical results in either case.

### Requirement 9: Tear down deployed resources

**User Story:** As a developer, I want to remove all deployed resources with a single command, so that I can return to a clean slate and avoid ongoing charges.

#### Acceptance Criteria

1. WHEN the developer runs `sam delete --profile K1-Kitstek-Billy`, THE SAM_CLI SHALL remove the CloudFormation stack containing the Dice_Roll_Function, the API_Gateway_Endpoint, and all associated AWS resources.
2. WHILE the stack deletion is in progress, THE SAM_CLI SHALL report deletion progress to the developer.
3. IF the named stack does not exist when `sam delete` is invoked, THEN THE SAM_CLI SHALL complete without error and report that there is nothing to delete.
4. IF deletion of any resource fails, THEN THE SAM_CLI SHALL exit with a non-zero status and emit an error identifying the resource that could not be deleted.
