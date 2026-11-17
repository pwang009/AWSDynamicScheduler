# Testing the Lambda

Run commands from the project root. Start with the offline tests. SAM local
invocation is optional and calls real AWS services.

## 1. Prepare Python 3.12

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r src/requirements.txt
```

If the project's `.venv` is already configured with these dependencies, reuse
it instead of recreating it.

## 2. Run offline unit tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass, ending in `OK`. AWS calls are mocked; no credentials,
Docker, AWS resources, inference charges, or email delivery are required.

To run only the mocked email tests:

```bash
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -p test_notifications.py -v
```

The suite covers rule parsing, date calculation, input validation, daylight
saving transitions, three-attempt creation retries, reconciliation, and
disabled email integration. Use these tests to simulate failures; do not
intentionally break production permissions or throttle live services.

## 3. Validate and build the SAM application

Install AWS SAM CLI if it is not already available, then run:

```bash
SAM_CLI_TELEMETRY=0 sam validate --lint --region us-east-1
SAM_CLI_TELEMETRY=0 sam build --region us-east-1
```

Expected: a valid template and `Build Succeeded`. These commands do not deploy
resources. Rebuild after source changes so local invocation uses current code.

For Terraform validation and mocked infrastructure tests, see
[tf/README.md](tf/README.md#build-initialize-and-validate). That deployment
creates both Lambdas and their supporting resources; use it instead of SAM
deployment, not alongside SAM for the same resources.

### Standalone test target Lambda

The [test_lambda/](test_lambda/) directory contains a Python 3.12 function named
`test-schedule-lambda-dynScheduler`. It logs the request ID and UTC execution
timestamp, then returns a confirmation and its received event. It does not call
other AWS services or modify application resources.

Validate/build its separate template from the project root:

```bash
sam validate --lint --template-file test_lambda/template.yaml --region us-east-1
sam build --template-file test_lambda/template.yaml --build-dir .aws-sam/test-target --region us-east-1
```

Invoke it locally with Docker and an empty event:

```bash
printf '{}\n' | sam local invoke TestScheduleFunction \
  --template .aws-sam/test-target/template.yaml --event -
```

Expected: a message containing `invoked successfully`, `request_id`,
`executed_at` in UTC, and `received_event: {}`. This standalone local test
does not require deployed AWS resources.

Only when authorized to create AWS resources, deploy the standalone target:

```bash
sam deploy --template-file .aws-sam/test-target/template.yaml \
  --stack-name test-schedule-target --resolve-s3 --capabilities CAPABILITY_IAM \
  --profile your-test-profile --region us-east-1
```

This creates the named Lambda and its logging role, not the scheduler or
monthly trigger. Deployment and cloud invocation may incur AWS charges.
If that function name already exists outside this stack, do not overwrite or
delete it; use an approved account/Region where the name is available.

After deployment, use `test-schedule-lambda-dynScheduler` as the scheduler
event's `target_lambda_name`, the scheduler stack's `TargetLambdaName`, and
the function-name suffix of `TARGET_LAMBDA_ARN`. Keep the account/Region
consistent. Verify scheduled invocations in the target's CloudWatch log group
`/aws/lambda/test-schedule-lambda-dynScheduler`.

## Invoke the deployed scheduler from your laptop

Use a JSON payload in [events/](events/) to invoke the scheduler already
deployed in CBSB. Install AWS CLI v2 and configure your AWS defaults or environment.
Docker, SAM, and a local environment-variable file are not needed for this
method; the deployed Lambda uses its own role and environment.

From the project root:

```bash
aws sts get-caller-identity

./invoke.sh currentdatetest.json
# An events/ prefix and filenames containing spaces are also accepted:
./invoke.sh "events/FriAfter1stSun copy.json"
```

[invoke.sh](invoke.sh) prints the Lambda response and writes it to the
project-root `response.json`, regardless of the caller's working directory.
It exits nonzero for invalid input, AWS CLI failures, or Lambda `FunctionError`
responses. It uses your configured AWS account/Region without explicit CLI flags.

Equivalent direct invocation:

```bash
aws lambda invoke \
  --function-name dynamic-lambda-scheduler-scheduler \
  --payload file://events/currentdatetest.json \
  --cli-binary-format raw-in-base64-out \
  response.json

cat response.json
```

Confirm the account before invoking. Your laptop's identity needs
`lambda:InvokeFunction` permission on the scheduler Lambda. The Lambda's
execution role supplies downstream service access.

This makes real Bedrock calls, writes state, and creates/replaces
`example-monthly-schedule` in the generated schedule group. Change the
`schedule_name` in the payload if you want a separate test name. Email remains
disabled. The target will run at the resolved future date/time.

Expected: no `FunctionError` in the CLI metadata, and `status: "created"` or
`"already_exists"` in the response with the schedule name/ARN and execution
date. HTTP status 200 alone does not mean the function succeeded.
`scheduled_at` is intentionally omitted so the first invocation resolves
against the current time; duplicate-job behavior still applies.

## 4. Optional: invoke locally against real AWS services

**Warning:** Docker runs the Lambda locally, but Bedrock, DynamoDB, and
EventBridge Scheduler are real AWS services. This test can incur charges,
write durable state, delete an existing same-name schedule in the test group,
and create a schedule that later invokes the target Lambda. Email stays
disabled. Never use a production schedule name/group for this test.

### Prerequisites

- Docker installed and running (`docker info` succeeds).
- AWS SAM CLI and AWS CLI installed.
- A dedicated commercial `us-east-1` test account/profile.
- Access to Amazon Nova Pro (`amazon.nova-pro-v1:0`) in `us-east-1`.
- An existing test DynamoDB table with string partition key `job_id`.
- An existing test Scheduler schedule group.
- An existing test target Lambda ending in `-dynScheduler`, safe to invoke
  with `{}`.
- A Scheduler execution role that trusts `scheduler.amazonaws.com` for the
  test group and can invoke that target Lambda.
- The local AWS identity can access that table, invoke the Bedrock model,
  get/delete/create schedules in the group, and pass the Scheduler execution role.

SAM local does not provision these resources or automatically assume the
Lambda role from the template. Use approved test resources. If you deploy the
full template separately, remember it also creates an active monthly trigger;
avoid unintended recurring invocations.

### Create the event file

Use [events/test.json](events/test.json), adjusting the following values if
testing against a different deployment:

```json
{
  "schedule_name": "example-monthly-schedule",
  "schedule_description": "the Friday after the 1st Sunday",
  "target_lambda_name": "test-schedule-lambda-dynScheduler",
  "execution_time": "09:00:00",
  "timezone": "America/New_York",
  "bedrock_model_id": "amazon.nova-pro-v1:0",
  "bedrock_region": "us-east-1"
}
```

Replace `test-schedule-lambda-dynScheduler` if using a different test target. Keep the
description within the supported rule type.

Choose a test-only `schedule_name` (1-64 letters, digits, underscores, dots,
or hyphens). Any existing schedule with that name in the configured group
will be deleted and replaced on a new logical job, even if its settings match.
There is a gap between deletion and creation; failed creation does not restore
the old schedule.

Omit `scheduled_at` for this manual test so it resolves against the current
time. Do not copy the literal `<aws.scheduler.scheduled-time>` placeholder
from the template: only the AWS Scheduler service substitutes it.

For a reproducible historical reference, an ISO timestamp with an offset or
`Z` may be supplied as `scheduled_at`. It must not be in the future, and its
resolved execution timestamp must still be in the future when creation runs.
Use offline tests for historical examples whose execution dates have passed.

### Create the local environment file

Create `env.local.json` at the project root:

```json
{
  "SchedulerFunction": {
    "STATE_TABLE": "your-test-state-table",
    "SCHEDULE_GROUP": "your-test-schedule-group",
    "TARGET_LAMBDA_ARN": "arn:aws:lambda:us-east-1:123456789012:function:test-schedule-lambda-dynScheduler",
    "SCHEDULER_ROLE_ARN": "arn:aws:iam::123456789012:role/your-test-scheduler-invocation-role",
    "ALLOWED_BEDROCK_MODEL_ID": "amazon.nova-pro-v1:0",
    "ALLOWED_BEDROCK_REGION": "us-east-1"
  }
}
```

Replace the example account, table, group, role, and target with existing test
resources. The ARN's target name must exactly match `target_lambda_name` in
the event. Do not put credentials in either JSON file.

Email environment variables are unnecessary while the import and call remain
commented out. Their supported settings are documented in
[README.md](README.md#email-support-intentionally-disabled).

### Confirm the account and invoke

Replace `your-test-profile` with your configured test profile:

```bash
aws sts get-caller-identity --profile your-test-profile --region us-east-1
```

Check that the returned account is the intended test account before continuing:

```bash
sam local invoke SchedulerFunction \
  --event events/test.json \
  --env-vars env.local.json \
  --profile your-test-profile \
  --region us-east-1
```

Expected response fields:

- `status`: `created` after creation/replacement, or `already_exists` when
  recovering or repeating the same job's confirmed replacement.
- `schedule_name` and `schedule_arn`: actual AWS schedule identifiers.
- `execution_date`, `execution_time`, and `timezone`: the resolved local date
  and requested time/zone.
- `notification_status`: `disabled`.

The execution timestamp must be strictly in the future. Errors must appear
explicitly rather than as a successful response.

### Verify the AWS schedule

Using the returned schedule name:

```bash
aws scheduler get-schedule \
  --name YOUR_RETURNED_SCHEDULE_NAME \
  --group-name your-test-schedule-group \
  --profile your-test-profile \
  --region us-east-1
```

Verify:

- `ScheduleExpression` matches the returned local date/time.
- `ScheduleExpressionTimezone` is `America/New_York`.
- `Target.Arn` identifies only the intended test Lambda, with `Input` equal
  to `"{}"`.
- `FlexibleTimeWindow.Mode` is `OFF`.
- `ActionAfterCompletion` is `DELETE`.

Before it executes, invoke locally again with the same event/environment.
Expected: `already_exists` and the same schedule ARN, not another schedule.
This duplicate-delivery protection does not apply to a new configuration or
reference month: a new job replaces the existing same-name schedule.
The first plan is persisted for that input configuration/reference month.
Reusing it after its execution time has passed will not automatically select
a fresh date.

To test replacement, use only a disposable test schedule. Change the
`execution_time` to another valid time and invoke again with the same
`schedule_name`. This is a new configuration/job: expect `created`, verify the
updated expression with `get-schedule`, and confirm no differently named
schedule was created. AWS may reuse the same ARN for the same group/name;
verify its settings, not an ARN change.

### Cleanup

If you do not want the target invoked, delete only the schedule created by
this test before its execution time:

```bash
aws scheduler delete-schedule \
  --name YOUR_RETURNED_SCHEDULE_NAME \
  --group-name your-test-schedule-group \
  --profile your-test-profile \
  --region us-east-1
```

Do not delete shared tables, roles, groups, or Lambda functions. Preserve
retry/notification state unless an operator explicitly approves removing
test-only records. After manual schedule deletion, replaying the same saved
job intentionally fails rather than recreating the missing schedule.

Remove the local event/environment files if no longer needed. Do not commit
credentials or sensitive account-specific configuration.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Docker connection error | Start Docker and check `docker info` |
| `Unapproved Bedrock model/Region pair` | Match the event to the approved model/Region environment values |
| Target ARN mismatch | Use the same target name in the event and environment ARN |
| Access denied or missing resources | Verify the test profile, resource names, IAM, model access, and Region |
| Creation budget exhausted or already in progress | Inspect persisted state and logs; after an interrupted run, allow the 150-second lease to expire before recovery |
| Resolved timestamp expired | Use mocked tests for historical dates; review the saved plan rather than deleting state to bypass protections |
| No email arrives | Expected: handler email integration is commented out |
