# Dynamic Lambda Scheduler

A Python 3.12 AWS Lambda application that turns one natural-language schedule
description into a future, one-time EventBridge Scheduler invocation.

The scheduler Lambda:

1. Validates the description, target Lambda, execution time, timezone, model,
   Region, and schedule name.
2. Uses Amazon Bedrock Nova Pro, a large language model (LLM), to interpret
   the requested natural-language calendar date.
3. Resolves recognized ordinal-weekday rules locally when possible.
4. Validates the real date and timezone-aware execution timestamp.
5. Persists an idempotent plan in DynamoDB.
6. Creates or replaces one enabled, one-time EventBridge Schedule.
7. Lets EventBridge invoke the configured target Lambda with `{}`.

The schedule is normally deleted after it runs. It is not a recurring
downstream schedule. The deployed application may also include an optional
recurring trigger that invokes the scheduler Lambda periodically; with the
default Terraform/SAM configuration this trigger is configured on the 1st of
each month at 1:00 AM Eastern. Terraform can disable it. The SAM template
creates it enabled unless its infrastructure is edited.

See [REQUIREMENTS.md](REQUIREMENTS.md) for detailed behavior and
[TEST.md](TEST.md) for validation procedures.

## Project overview

| Component | Responsibility |
| --- | --- |
| [src/handler.py](src/handler.py) | Event validation, reference time, job identity, and workflow coordination |
| [src/bedrock_parser.py](src/bedrock_parser.py) | Local ordinal-weekday resolution and Bedrock calendar interpretation |
| [src/schedule_description.py](src/schedule_description.py) | Date, DST, timezone, and future-timestamp validation |
| [src/scheduler.py](src/scheduler.py) | DynamoDB coordination and EventBridge Scheduler create/get/delete operations |
| [src/state.py](src/state.py) | Durable plans, locks, creation attempts, and schedule ownership records |
| [src/notifications.py](src/notifications.py) | Optional SES support; not called by the current handler |
| [test_lambda/app.py](test_lambda/app.py) | Harmless target Lambda that logs and echoes `{}` |
| [template.yaml](template.yaml) | SAM deployment and monthly trigger resources |
| [tf/](tf/) | Terraform deployment alternative |

AWS resources include a DynamoDB state table, two Scheduler groups, IAM roles,
a scheduler Lambda, an optional test target Lambda, and the recurring trigger
when enabled. Runtime-created schedules belong in the generated-schedule
group. The recurring trigger belongs in a separate group.

## Input and behavior

An invocation is a single schedule request:

```json
{
  "schedule_name": "example-monthly-schedule",
  "schedule_description": "the Friday after the 1st Sunday",
  "target_lambda_name": "example-dynScheduler",
  "execution_time": "09:00:00",
  "timezone": "America/New_York",
  "bedrock_model_id": "amazon.nova-pro-v1:0",
  "bedrock_region": "us-east-1"
}
```

Only the deployment-approved target and model/Region pair are accepted.
Descriptions use open-ended natural language, not a fixed pattern catalog.
Bedrock returns exactly `{"execution_date":"YYYY-MM-DD"}`. Python validates the
real calendar date, combines it with the unchanged input time/timezone, and
rejects past/equal timestamps, DST gaps, and repeated times. Future dates can
cross month/year boundaries. The target receives `{}`.
"Today" means the reference date in the input timezone and is valid if the
input execution time is still ahead. For example, at 14:28 Eastern, "today"
with `15:00:00` runs at 15:00 that day. Bedrock is instructed to return fixed
dates even when their time has passed; Python rejects those timestamps rather
than silently advancing them.

For a November 17, 2026 reference date, examples include:

| Description | Expected date |
| --- | --- |
| the Friday after the 1st Sunday | 2026-12-11 |
| 2nd of the month after this quarter end | 2027-01-02 |
| two days after this quarter ends | 2027-01-02 |
| the last day of this quarter | 2026-12-31 |

Calendar quarters end March 31, June 30, September 30, and December 31.
"This quarter" contains the local reference date; "next quarter" follows it.
Recognized recurring ordinal-weekday rules, such as "the Friday after the 1st
Sunday", are resolved locally and advance to the next future month when needed.
Recurring descriptions select the next future occurrence; explicit dates or
explicit "this"/"next" periods are not silently rolled forward if already past.
Ambiguous wording, unspecified fiscal/holiday calendars, conflicting times or
timezones, and instructions to override configuration must produce model errors.
No system can guarantee interpretation of every wording: Python validates
calendar/timestamp safety but cannot independently verify the model's semantic
interpretation. Representative wording needs live model testing before reliance.

Before sending a description to Bedrock, the handler appends
`, current date: MM/DD/YYYY`, for example
`the Friday after the 1st Sunday, current date: 11/17/2026`.
The date uses the input timezone and the invocation's reference timestamp
(`scheduled_at` when supplied, otherwise the current invocation time).
The original description is preserved in saved plans and responses. Existing
saved plans bypass Bedrock. The request also supplies the precise local reference
timestamp, execution time, and timezone so the model can evaluate same-day times.
Retries continue to reuse saved dates rather than reinterpret descriptions.

`schedule_name` is required: 1-64 letters, digits, underscores, dots, or
hyphens. For each new logical job (input configuration/reference month, or an
explicit request ID as described below),
delete any schedule with that name in the configured group, then create the
new schedule. This applies even when the previous schedule has matching
settings. Other groups are untouched. Deletion and creation are not atomic:
if creation fails after deletion, the old schedule is not restored.

### Intentionally rerun the same schedule

Add an optional `request_id` to the event, for example:

```json
"request_id": "manual-2026-11-17-001"
```

Use a **new ID for each intentional rerun**, even with the same schedule name
and description. Each gets a fresh Bedrock interpretation, saved plan,
three-attempt creation budget, and AWS idempotency token. This can replace the
current same-name schedule. IDs allow 1-64 letters, digits, underscores, dots,
or hyphens, and are included in saved plans and responses.

For retries, reuse the **same ID and unchanged inputs**. Explicit-ID jobs are
independent of the reference month, so retries across a month boundary still
reuse the original saved plan. A previously successful request whose schedule
has since been replaced remains blocked; give an intentional replacement a
new ID instead of removing that safeguard. Do not generate IDs inside Lambda.

Without `request_id`, the existing input-configuration/reference-month identity
is unchanged, including existing DynamoDB records and monthly trigger behavior.
No state deletion or Terraform changes are needed.

## Local checks

See [TEST.md](TEST.md) for step-by-step offline tests and optional SAM local
invocation with a JSON payload and environment file.

Use Python 3.12 and a current AWS SAM CLI:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r src/requirements.txt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
SAM_CLI_TELEMETRY=0 sam validate --lint --region us-east-1
sam build
```

All unit-test AWS calls are mocked. These commands do not deploy resources.

## Deployment configuration

Choose SAM or Terraform. Do not deploy both implementations into the same
account and Region. Deployment creates billable AWS resources and can replace
a same-name schedule; never run it until an AWS account, profile, and change
plan have been approved.

### Prerequisites

- Python 3.12 and pip.
- AWS CLI v2 with credentials for an approved commercial `us-east-1` account.
- AWS SAM CLI for SAM deployment, or Terraform 1.7 or later for Terraform.
- Access to Amazon Nova Pro (`amazon.nova-pro-v1:0`) in `us-east-1`.
- IAM permission to create the resources listed in [template.yaml](template.yaml)
  or [tf/main.tf](tf/main.tf).
- For SAM, an existing unqualified target Lambda whose name ends in
  `-dynScheduler`, in the same account and Region.

Verify the AWS identity and Region before deployment:

```bash
export AWS_PROFILE=your-approved-profile
aws sts get-caller-identity
aws configure get region
```

Both deployment paths require commercial `us-east-1`; GovCloud-to-commercial
cross-partition deployment is not supported.

### Option 1: Deploy with SAM

For the simplest test setup, first build and deploy the standalone test target:

```bash
sam validate --lint --template-file test_lambda/template.yaml --region us-east-1
sam build \
  --template-file test_lambda/template.yaml \
  --build-dir .aws-sam/test-target \
  --region us-east-1
sam deploy \
  --template-file .aws-sam/test-target/template.yaml \
  --stack-name test-schedule-target \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --region us-east-1 \
  --profile your-approved-profile
```

Confirm the target name:

```bash
aws lambda get-function \
  --function-name test-schedule-lambda-dynScheduler \
  --region us-east-1 \
  --profile your-approved-profile \
  --query 'Configuration.[FunctionName,Runtime]' \
  --output table
```

Build and validate the scheduler:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r src/requirements.txt
SAM_CLI_TELEMETRY=0 sam validate --lint --region us-east-1
sam build
```

Deploy interactively for the first installation:

```bash
sam deploy --guided \
  --region us-east-1 \
  --profile your-approved-profile
```

At the SAM prompts, provide:

- Stack name, for example `dynamic-lambda-scheduler`.
- `TargetLambdaName`: `test-schedule-lambda-dynScheduler` for the test target.
- `ScheduleInput`: a JSON object containing the target name and the desired
  schedule fields. Include
  `"scheduled_at":"<aws.scheduler.scheduled-time>"` for monthly-trigger
  invocations; SAM passes this literal to Scheduler, which replaces it.
- `BedrockModelId`: `amazon.nova-pro-v1:0`.
- `BedrockRegion`: `us-east-1`.
- IAM capability confirmation.

For a non-interactive repeat deployment, either retain the guided
`samconfig.toml` or pass the parameters explicitly:

```bash
sam deploy \
  --stack-name dynamic-lambda-scheduler \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --region us-east-1 \
  --profile your-approved-profile \
  --parameter-overrides \
    TargetLambdaName=test-schedule-lambda-dynScheduler \
    'ScheduleInput={"schedule_name":"example-monthly-schedule","schedule_description":"the Friday after the 1st Sunday","target_lambda_name":"test-schedule-lambda-dynScheduler","execution_time":"09:00:00","timezone":"America/New_York","bedrock_model_id":"amazon.nova-pro-v1:0","bedrock_region":"us-east-1","scheduled_at":"<aws.scheduler.scheduled-time>"}' \
    BedrockModelId=amazon.nova-pro-v1:0 \
    BedrockRegion=us-east-1
```

Inspect the deployment and invoke the function by name:

```bash
aws cloudformation describe-stacks \
  --stack-name dynamic-lambda-scheduler \
  --region us-east-1 \
  --profile your-approved-profile \
  --query 'Stacks[0].Outputs'

SCHEDULER_ARN="$(aws cloudformation describe-stacks \
  --stack-name dynamic-lambda-scheduler \
  --region us-east-1 \
  --profile your-approved-profile \
  --query 'Stacks[0].Outputs[?OutputKey==`SchedulerFunctionArn`].OutputValue' \
  --output text)"
aws lambda invoke \
  --function-name "$SCHEDULER_ARN" \
  --payload file://events/FriAfter1stSun.json \
  --cli-binary-format raw-in-base64-out \
  --region us-east-1 \
  --profile your-approved-profile \
  response.json
cat response.json
```

For a deployed script-based invocation, [invoke.sh](invoke.sh) accepts only
events under `events/`, but its function name must match the deployed Lambda.
It is configured for the Terraform function name
`dynamic-lambda-scheduler-scheduler` unless that variable is changed.

### Option 2: Deploy with Terraform

The Terraform deployment creates the test target and all scheduler resources.
Detailed configuration and recovery steps are in [tf/README.md](tf/README.md).

From the project root:

```bash
PYTHON="$PWD/.venv/bin/python" bash tf/build.sh
cp tf/terraform.tfvars.example tf/terraform.tfvars
terraform -chdir=tf init
terraform -chdir=tf fmt -check
terraform -chdir=tf validate
terraform -chdir=tf test
```

Edit `tf/terraform.tfvars` with the approved project name, schedule, time,
timezone, Bedrock pair, and whether the monthly trigger should start enabled.
Review credentials and the plan:

```bash
export AWS_PROFILE=your-approved-profile
aws sts get-caller-identity
terraform -chdir=tf plan -out=deployment.tfplan
```

Apply only after reviewing the account, resource changes, IAM permissions,
and monthly-trigger state:

```bash
terraform -chdir=tf apply deployment.tfplan
```

Invoke the deployed scheduler and inspect the result:

```bash
terraform -chdir=tf output -raw manual_test_payload \
  > tf/.build/test-event.json
aws lambda invoke \
  --function-name "$(terraform -chdir=tf output -raw scheduler_function_name)" \
  --payload file://tf/.build/test-event.json \
  --cli-binary-format raw-in-base64-out \
  --region us-east-1 \
  --profile your-approved-profile \
  tf/.build/scheduler-response.json
cat tf/.build/scheduler-response.json
```

Use `terraform -chdir=tf output` to find the state table, schedule group,
target function, trigger, and role names. The invocation calls real Bedrock,
writes DynamoDB state, and creates or replaces a real schedule.

### Post-deployment verification

For either deployment path:

1. Confirm the scheduler Lambda exists and CloudWatch has a log group.
2. Confirm Bedrock model access is enabled for the account/Region.
3. Invoke manually and verify the response contains `status: created` or
   `already_exists` and a future `execution_timestamp`.
4. Inspect the configured schedule group for the returned schedule name.
5. Invoke the test target directly with `{}` and confirm it logs success.
6. Check Scheduler and Lambda CloudWatch logs for permission, Bedrock,
   timeout, DST, or state errors.
7. If the monthly trigger is enabled, review its next run and timezone.

Do not treat a successful AWS CLI invocation as proof that the Lambda
succeeded. Read `response.json` or the saved response file and inspect the
Lambda logs.

The template creates the function, a retained DynamoDB state table, separate
groups for generated schedules and the monthly trigger, invocation roles, and
the monthly trigger. Keeping the trigger separate prevents input names from
replacing it. The function can get, delete, and create schedules only in its
generated-schedule group. It restricts invocation to
the configured target and inference to the configured foundation model.
AWS's default target execution retry policy applies to the created one-time
schedules; it is separate from the three-attempt creation limit.

The deployment account/Region supplies Scheduler and DynamoDB configuration.
The event's `bedrock_region` affects only Bedrock. GovCloud-to-commercial
cross-partition deployment is not supported.

## Optional VPC attachment

VPC attachment is not required for this application's current AWS API calls.
Use it if organizational policy requires it or the function needs private
resources such as an internal API or database. A Lambda without VPC attachment
is not publicly invocable by default; IAM and resource policies control access.
The current SAM and Terraform configurations do not attach either Lambda to a
VPC or provision VPC networking.

If attachment becomes necessary:

1. **Select the network.** Supply the existing VPC's private subnet IDs and
   Lambda security group IDs. Prefer subnets in at least two Availability Zones,
   with sufficient free IP addresses. Attach each Lambda only if it needs the
   VPC; the simple test target does not need network access for its current
   behavior.
2. **Update infrastructure.** Add a `vpc_config` block with `subnet_ids` and
   `security_group_ids` to each applicable Terraform Lambda, or `VpcConfig`
   with `SubnetIds` and `SecurityGroupIds` in SAM. Expose deployment inputs for
   these IDs rather than hardcoding an existing function's configuration.
3. **Grant network-interface permissions.** The attached Lambda's execution
   role needs the permissions in `AWSLambdaVPCAccessExecutionRole`, or an
   equivalent reviewed policy: `ec2:CreateNetworkInterface`,
   `ec2:DescribeNetworkInterfaces`, `ec2:DescribeSubnets`,
   `ec2:DeleteNetworkInterface`, `ec2:AssignPrivateIpAddresses`, and
   `ec2:UnassignPrivateIpAddresses`. Preserve its existing service permissions.
4. **Provide AWS API connectivity.** The scheduler must reach Bedrock Runtime,
   DynamoDB, and EventBridge Scheduler over HTTPS. Choose an approved path:
   - Private-subnet routes to a NAT gateway with a functioning public-subnet
     internet gateway route; or
   - Appropriate VPC endpoints where supported in the selected Region:
     Bedrock Runtime and Scheduler interface endpoints, and a DynamoDB gateway
     endpoint associated with the Lambda subnet route tables.
   Verify endpoint availability and endpoint policies for the actual APIs.
   If a required API has no suitable endpoint, provide approved NAT/proxy
   connectivity. Enabling SES later also requires a path to the HTTPS SES API
   used by `boto3.client("sesv2")`; an SMTP endpoint is not a substitute.
5. **Configure DNS and traffic rules.** Enable VPC DNS support/hostnames and
   private DNS on interface endpoints. Allow outbound TCP 443 from the Lambda
   security group to the required destinations. Interface-endpoint security
   groups must allow inbound TCP 443 from the Lambda security group. Ensure
   network ACLs permit requests and return traffic. Attaching Lambda to a
   public subnet does not give it a public IP address or internet access.
6. **Validate in AWS.** After an authorized deployment, test an actual scheduler
   invocation and check logs for DNS, connection, permission, and timeout
   failures. Mocked unit tests and SAM local invocation do not prove that the
   deployed function's VPC routing works.

EventBridge Scheduler invokes Lambda through the AWS service API; no inbound
security-group rule or Lambda VPC endpoint is needed merely to receive that
invocation. If application code later calls other AWS APIs, evaluate their
network paths separately.

NAT gateways and interface endpoints add ongoing charges. Do not copy subnet
or security group IDs from another Lambda without checking its routes, service
access, and policy requirements.

## Retry and recovery behavior

The first resolved plan is persisted for an input configuration/reference
month. Monthly redeliveries carry the same `scheduled_at`; manual invocations
without that field reuse any plan for the current month/configuration. Retry
creation using the same inputs/reference, not a new description.

There are at most three creation attempts across redeliveries. SDK retries
are disabled; transient creation errors back off one then two seconds.
Conditional DynamoDB writes reserve attempts and a 150-second lease. After a
crash, wait for that lease to expire before manual recovery. A timeout can
have created the schedule: the code checks for an equivalent schedule carrying
the current job's ownership marker before attempting another creation, even
when the budget is spent. Duplicate deliveries of the same job reuse that
replacement instead of deleting it again. Each new job has a new idempotency
token and retry budget even when it uses the same schedule name. A per-name
lock serializes replacements in the same group.
Permanent errors, exhausted budgets, and conflicting same-job schedules surface as
Lambda failures. Inspect CloudWatch logs; no recovery worker or alarms are
created.

Do not delete/reset state casually: it protects against duplicate creation
and preserves the retry limit. A completed, automatically deleted schedule
must not be recreated by replaying its old job.

## Email support: intentionally disabled

The SES implementation is in [src/notifications.py](src/notifications.py).
The import and call are commented out in [src/handler.py](src/handler.py):

```python
# from notifications import send_creation_email
# send_creation_email(result, state, boto3.client("sesv2", config=config))
```

The handler returns `"notification_status": "disabled"` and never calls SES.
Email settings are not required until the integration is enabled.

| Environment variable | Supported configuration |
| --- | --- |
| `EMAIL_PROVIDER` | `ses` |
| `EMAIL_SENDER` | Verified sender address |
| `EMAIL_RECIPIENTS` | JSON array, e.g. `["recipient@example.com"]` |
| `EMAIL_MAX_ATTEMPTS` | Total attempts, integer 1-3 |
| `EMAIL_RETRY_BASE_SECONDS` | Backoff base, number 0-2 |
| `EMAIL_RECOVERY_POLICY` | `manual` |

SAM parameters with corresponding CamelCase names populate these variables.
Before enabling, configure the actual values, verify SES identities and
sandbox restrictions, and add narrowly scoped `ses:SendEmail` permission for
the sender identity to the function role. Uncomment the import and call, and
replace the handler's disabled status/logging with the actual delivery
outcome; do not report `"disabled"` after sending.

The email function persists attempts and the SES message ID, skips recorded
deliveries, and retries transient failures within the configured budget.
Failures do not remove or recreate the schedule. With `manual` recovery,
inspect the schedule's DynamoDB item and logs before deliberately retrying a
pending notification. An interrupted attempt consumes its reserved budget.
Exhausted/terminal notifications require an operator-reviewed state repair;
there is no automatic replay service. A crash or uncertain SES response can
produce duplicate email, because SES sending and DynamoDB updates are not
atomic.
