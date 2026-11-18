# Dynamic Lambda Scheduler

Python 3.12 Lambda for one description/target pair. A monthly trigger runs at
1:00 AM `America/New_York` on the 1st. Nova Pro interprets the description and
returns a date, Python validates the timestamp, and EventBridge Scheduler creates a one-time
invocation. See [REQUIREMENTS.md](REQUIREMENTS.md) for the full behavior.

## Input

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

For an all-resources Terraform alternative (including the test target), see
[tf/README.md](tf/README.md). Choose Terraform or SAM; do not deploy both against
the same resources.

Deployment is deliberately not performed here. Before an authorized
`sam deploy --guided --region us-east-1`, supply:

- `TargetLambdaName`: an existing, unqualified Lambda name in the same account
  and Region, ending in `-dynScheduler`.
- `ScheduleInput`: the JSON above with the actual target name, plus
  `"scheduled_at":"<aws.scheduler.scheduled-time>"` for the monthly trigger.
  Scheduler replaces that context attribute at invocation time.
- Access to `amazon.nova-pro-v1:0` in commercial `us-east-1`.
- Any production failure destinations/alarms and downstream execution retry
  settings required by your operations policy.

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
