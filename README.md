# Dynamic Lambda Scheduler

Python 3.12 Lambda for one description/target pair. A monthly trigger runs at
1:00 AM `America/New_York` on the 1st. Nova Pro interprets a monthly rule,
Python resolves the future date, and EventBridge Scheduler creates a one-time
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
Descriptions must mean a weekday strictly after an ordinal weekday in a
month. Unsupported/ambiguous rules, missing ordinal occurrences, and DST gaps
or repeated times are explicit errors. Future dates can cross month/year
boundaries. The target receives `{}`.

`schedule_name` is required: 1-64 letters, digits, underscores, dots, or
hyphens. For each new logical job (input configuration/reference month),
delete any schedule with that name in the configured group, then create the
new schedule. This applies even when the previous schedule has matching
settings. Other groups are untouched. Deletion and creation are not atomic:
if creation fails after deletion, the old schedule is not restored.

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
