# Dynamic Lambda Scheduler

## Confirmed requirements

- Implement an AWS Lambda using Python 3.12.
- Support exactly one description/target pair per invocation; do not implement
  batch configuration. Manual invocation is supported, and a separate recurring
  EventBridge trigger may invoke the Lambda on a configured schedule.
- Deploy in commercial `us-east-1` using AWS SAM or Terraform and DynamoDB-backed creation
  attempt tracking, as confirmed for the initial implementation.
- Provide an alternative Terraform deployment in `tf/` for both Lambdas and
  their supporting IAM, logging, DynamoDB, schedule groups, and monthly
  trigger. Terraform and SAM must not manage the same resources simultaneously.
- Optionally trigger the scheduler Lambda at 1:00 AM on the 1st of each month
  in `America/New_York` (EST in winter, EDT in summer). Terraform exposes this
  as `monthly_trigger_enabled`; SAM creates the trigger enabled.
- Accept:
  1. A schedule description, for example "the Friday after the 1st Sunday".
  2. The name of a target Lambda to invoke. Its name must end in
     `-dynScheduler`.
  3. An execution time in 24-hour `HH:MM:SS` format, interpreted in the
     input timezone.
  4. An IANA timezone identifier, for example `America/New_York` or `UTC`.
  5. A Bedrock model ID; use `amazon.nova-pro-v1:0` (Amazon Nova Pro) for
     this schedule.
  6. A Bedrock Region; use `us-east-1` for this schedule.
  7. A schedule name, using 1-64 letters, digits, underscores, dots, or hyphens.
- Accept optional `request_id` for intentional reruns: 1-64 letters, digits,
  underscores, dots, or hyphens. A new ID gets a fresh plan, creation-attempt
  budget, and AWS idempotency token without changing the schedule name.
- For a new logical job, delete any schedule with the input name in the
  configured Scheduler group, then create the new schedule under that name.
  Other groups must not be modified.
- Use `America/New_York` as the timezone input for the requested US Eastern
  schedule. It observes EST (UTC-05:00) in winter and EDT (UTC-04:00) in summer,
  rather than fixed EST year-round.
- Translate the description into a calendar date and create a one-time Amazon
  EventBridge Scheduler schedule that invokes the target Lambda on that date.
- Use an Amazon Bedrock LLM to resolve open-ended descriptions to a calendar
  date, rather than restricting input to a fixed rule catalog. Python strictly
  validates the date and combined execution timestamp; it does not independently
  verify the model's interpretation or calendar arithmetic.
- Return the created schedule's identity and resolved execution date.
- Create the next future occurrence of the rule. Execution may be in the
  current month or a later month, including a later year.
- If schedule creation fails, allow at most three attempts total: the initial
  attempt plus two retries.
- Implement a creation-notification email function, but leave its import and
  call in the main handler commented out. Email is intentionally disabled in
  the initial implementation; return `notification_status: "disabled"`.
- Configure email provider, sender, recipients, and notification retry/recovery
  policy through environment variables, not event inputs or hardcoded values.
- Example: the December 1, 2026 run resolves the example description to
  December 11, 2026. A November 17, 2026 run also resolves to December 11
  because November's occurrence has passed. Use ISO dates
  (`2026-12-11`) in machine-readable input and output.

## Program structure

The implementation uses this structure:

```text
eventBridgeSchedule/
  AGENT.md
  REQUIREMENTS.md
  template.yaml              # Infrastructure, IAM roles, and monthly trigger
  src/
    handler.py               # Validate event and coordinate the workflow
    bedrock_parser.py        # Invoke Bedrock to interpret descriptions
    schedule_description.py  # Validate model dates and local execution timestamps
    scheduler.py             # Create the one-time schedule using boto3
    notifications.py         # SES email support; handler integration commented out
    state.py                 # Persist plans, retry budgets, and delivery state
    requirements.txt         # Runtime dependencies
  tests/
    test_handler.py
    test_bedrock_parser.py
    test_schedule_description.py
    test_scheduler.py
    test_notifications.py
  README.md                  # Configuration, supported wording, deployment
```

Keep date and timestamp validation independent of AWS calls so they
can be tested without deploying resources. Isolate Bedrock, Scheduler, and email
calls so unit tests can mock them. Use AWS SAM for infrastructure.

## Proposed input and output

The monthly trigger supplies the schedule name, description, target Lambda name,
execution time, timezone, Bedrock model ID, and Bedrock Region as a constant JSON payload:

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

`execution_time` is required; reject missing or invalid values explicitly.
Accept hours `00`-`23`, minutes `00`-`59`, and seconds `00`-`59`.
The example time is illustrative, not a default.

`timezone` is required; reject missing or unrecognized IANA identifiers
explicitly. Do not silently default to UTC or the host's local timezone.
For the requested US Eastern schedule, supply `America/New_York` explicitly;
it is not an implicit default. Use the IANA identifier, not the abbreviation
`EST`, to preserve daylight saving behavior.

`bedrock_model_id` and `bedrock_region` are required inputs, not implicit
defaults. Construct the Bedrock Runtime client for the input Region and invoke
the input model ID. Validate the pair against deployment-approved models and
Regions; reject missing, unsupported, or unauthorized values explicitly.
Do not silently substitute another model, inference profile, or Region.

The deployment account and Scheduler invocation role remain deployment
configuration; the deployment Region is commercial `us-east-1`.
`bedrock_region` selects only the inference endpoint; it must not
change where schedules are created or which Lambda is invoked.

The monthly trigger also supplies `scheduled_at` using EventBridge Scheduler's
`<aws.scheduler.scheduled-time>` context attribute. Preserve that reference
across redeliveries. Persist the first resolved plan for the input configuration
and reference month so repeated deliveries cannot choose a different date.
Manual calls may omit `scheduled_at`; they use the invocation time as their
reference and reuse any existing plan for that configuration/reference month.

When `request_id` is present, include it in the validated inputs and derive
the job ID from those inputs alone, excluding the reference month. Reusing the
same ID and unchanged inputs must reuse the saved plan across month boundaries.
Changing the ID creates a new logical request, with a new Bedrock call and
independent durable creation attempts and AWS tokens. Preserve the existing
hash input exactly when the ID is omitted so deployed jobs remain compatible.
Return the ID in saved plans and responses; reject invalid IDs before AWS calls.
Do not generate IDs automatically in Lambda or reset/delete previous state.
The same previously successful request must still refuse recreation if its
schedule is missing or has been replaced, protecting newer replacements from
retries of that completed request. An intentional rerun must use a new ID.

Example success response fields:

```json
{
  "status": "created",
  "target_lambda_name": "example-dynScheduler",
  "execution_date": "2026-12-11",
  "execution_time": "09:00:00",
  "timezone": "America/New_York"
}
```

The actual response must also contain the schedule name and schedule ARN
returned by AWS. Report success only after AWS confirms creation, or report a
distinct `already_exists` result after verifying the same job's replacement.
Validate `schedule_name` before AWS calls; it is not generated from the target
or timestamp.

## Translating a description into a date

### LLM parsing with Amazon Bedrock

Call an on-demand model through Amazon Bedrock Runtime using the AWS SDK.
Bedrock is an AWS-managed service; no dedicated model/server deployment or
provisioned capacity is required for this design.

The workflow is:

1. Validate the required input fields.
2. Append `, current date: MM/DD/YYYY` to the description before sending it to
   Bedrock, using the invocation's reference date in the input timezone.
   For example: `2nd of the month after this quarter end, current date: 11/17/2026`.
   Use `scheduled_at` as the reference when supplied; otherwise use the current
   invocation timestamp. Preserve the original input description in plans and
   responses. Supply the precise local reference timestamp, execution time,
   and timezone separately as context.
3. Ask Bedrock to interpret the wording and return a concrete calendar date.
4. Require exactly one `execution_date` string in strict `YYYY-MM-DD` format;
   reject extra/missing fields, incorrect types, and impossible calendar dates.
5. Accept open-ended wording, including quarter ends, rather than enforcing a
   fixed natural-language pattern catalog.
6. Combine the date with the input time and timezone, validate the execution
   timestamp, and delete any existing same-name schedule before creating the
   replacement, except when recovering the same job's confirmed replacement.
7. Leave the email call commented out and report notifications as disabled.
   When explicitly enabled later, send only after confirming creation.

For example, "2nd of the month after this quarter end", with a local reference
date of November 17, 2026, should produce:

```json
{
  "execution_date": "2027-01-02"
}
```

Calendar quarters end March 31, June 30, September 30, and December 31.
"This quarter" contains the reference date in the input timezone;
"next quarter" is the following quarter. On November 17, 2026, "two days after
this quarter ends" also means January 2, 2027, and "the last day of this quarter"
means December 31, 2026. Other unambiguous wording is not excluded by a pattern
allowlist.

The LLM must return `{"error":"<specific reason>"}` for ambiguity, missing
fiscal/holiday calendar information, conflicting time/timezone instructions,
nonexistent ordinal occurrences, or descriptions with no possible recurring occurrence.
Surface that reason explicitly and create no schedule. Open-ended wording
does not guarantee that every phrase is interpretable.

Treat the description and model response as untrusted data. Do not follow
instructions embedded in the description or allow the model to choose the
target Lambda, execution time, timezone, IAM role, or AWS API operations.
The model has no tools or direct ability to create schedules.

Surface Bedrock failures, refused or truncated responses, invalid JSON, and
invalid dates explicitly. Do not create a schedule on any such failure, and
do not silently fall back to a guessed rule or date. Structural validation
does not guarantee semantic accuracy, so test representative wording and
ambiguous requests against the selected model before deployment.

### Bedrock configuration and access

- Use the required `bedrock_model_id` and `bedrock_region` event inputs.
  The selected pair is Amazon Nova Pro (`amazon.nova-pro-v1:0`) in `us-east-1`.
  Configure approved model/Region pairs, request timeout, and bounded retry
  policy through deployment configuration.
- Select a model available for the chosen Region/inference configuration and
  complete any applicable model-access requirements.
- Request JSON through the prompt and validate it in application code. Nova
  Pro does not support Bedrock's native Structured Outputs feature; do not
  assume the service enforces the response schema.
- Deploy in commercial `us-east-1`; this implementation does not support a
  GovCloud-to-commercial cross-partition integration.
- Grant the scheduler Lambda execution role narrowly scoped
  `bedrock:InvokeModel` permission for the chosen model/inference resources.
- Account for inference latency in the Lambda timeout and for usage-based
  Bedrock costs.
- Keep prompts and the expected response schema versioned with the source.
- Unit tests must mock Bedrock; live model tests require explicit
  authorization because they incur usage costs.

### Date interpretation and Python validation

Bedrock performs calendar interpretation and chooses the next future date at
the supplied execution time. For recurring wording without an explicit period,
advance to its next occurrence when necessary. Keep existing weekday semantics:
"after" is strict (the same weekday means seven days later), and offsets may
cross months/years. Explicit dates and "this"/"next" periods must not be silently
advanced if their execution is already past.
Recognized ordinal-weekday rules such as "the Friday after the 1st Sunday" are
resolved in Python from the reference month and timezone, independent of Bedrock.

"Today" must resolve to the local reference date, not be rejected solely for
being the same calendar date. At 14:28 Eastern on November 17, 2026, "today"
with execution time `15:00:00` means `2026-11-17T15:00:00-05:00`.
Bedrock returns fixed dates, including elapsed ones, and Python enforces
timestamp eligibility; equal/past times fail without rolling the date forward.

Python validates with standard-library `date`, `datetime`, and `zoneinfo`:

1. Validate the exact response fields and strict date string, then reject
   impossible dates with `date.fromisoformat`.
2. Combine the date with the input execution time and input timezone.
3. Reject nonexistent or repeated local times using UTC round trips and folds.
4. Require a timezone-aware reference and a timestamp strictly later than it.
   If the model returns a past/equal timestamp, fail explicitly rather than
   rolling forward or silently retrying inference.

Python cannot prove that a valid future date matches the description's meaning.
Mocked tests validate the application contract, not live model interpretation.
Representative live wording tests require explicit authorization.

### Worked example

- Scheduler Lambda run: December 1, 2026 at 1:00 AM `America/New_York`.
- December's first Sunday: December 6, 2026.
- Friday strictly after that Sunday: December 11, 2026.
- With input time `09:00:00`, create a schedule for December 11, 2026 at
  9:00 AM in the input timezone.
- On November 17, 2026, November's occurrence would be November 6, already in
  the past. Advance to December and create the December 11 schedule.

Future-occurrence behavior:

- Start with the current anchor month, including manual or delayed invocations,
  and advance as needed to find the next future execution timestamp.
- Evaluate "today" and "passed" in the input timezone, not the host's
  implicit timezone.
- Allow weekday offsets to spill into the following month or year.
- Instruct Bedrock to reject nonexistent ordinal occurrences explicitly.
- Creation retries must reuse the resolved timestamp rather than select a
  different occurrence. If it is no longer in the future, report an explicit
  failure instead of silently rescheduling it.

## Monthly trigger

Configure a recurring EventBridge Scheduler trigger using
`cron(0 1 1 * ? *)` with `ScheduleExpressionTimezone` set to
`America/New_York` and the flexible time window disabled. Its target is the
scheduler Lambda, not the downstream `-dynScheduler` Lambda. The trigger's
timezone is fixed independently of the target execution timezone input.

## Creating the AWS schedule

Use Amazon EventBridge Scheduler (`boto3.client("scheduler")`), not a recurring
EventBridge rule for the target invocation:

- Create a one-time expression using the resolved date and input execution
  time, for example `at(2026-12-11T09:00:00)`.
- Set `ScheduleExpressionTimezone` explicitly to the input `timezone`.
- Disable the flexible time window for execution at the specified time.
- Set the target Lambda ARN and a Scheduler role permitted to invoke it.
- Configure `ActionAfterCompletion` as `DELETE`.
- Use the required input `schedule_name` verbatim.
- For a new logical job (input configuration/reference month without an explicit
  ID, or input configuration including an explicit request ID), delete the
  existing same-name schedule in the configured group, even if its settings
  match. Do not delete the replacement again on duplicate delivery of the
  same job. Mark schedules with a job-specific description and verify their
  expression, timezone, target, and invocation settings when reconciling.
- Persist attempt and notification state per job, not just per schedule name.
  Use a job-specific creation token so a later month or new explicit request
  can replace the same name.
  Serialize same-name replacements using a group/name DynamoDB lease.
- Fail explicitly if deletion fails permanently. Deletion and creation are
  not atomic: if creation fails after deletion, the old schedule is not
  automatically restored.
- Keep the monthly trigger in a separate group so it cannot be replaced by
  the input schedule name.
- Target code must tolerate duplicate deliveries; scheduling does not
  guarantee exactly-once execution.

The scheduler Lambda needs get/create/delete Scheduler permissions scoped to
the generated-schedule group and
`iam:PassRole` for the specific invocation role. That role must trust
`scheduler.amazonaws.com` and allow `lambda:InvokeFunction` only on the
intended target resources.

## Schedule creation retries

- Permit at most three creation attempts total (initial attempt plus two
  retries), stopping immediately after confirmed creation or verification of
  an equivalent existing schedule.
- Retry transient creation failures with bounded backoff. Invalid input,
  unsupported rules, resolved execution times that expire during retries, and permanent
  authorization/configuration failures must surface explicitly rather than
  being retried as if a valid schedule could be created.
- Reuse the same validated rule, resolved timestamp, schedule name, and
  idempotency token across attempts. After a timeout or uncertain response,
  reconcile the schedule's existence before trying creation again.
- Coordinate SDK retries and Lambda/trigger redelivery so they do not
  multiply the three-attempt budget for the same logical schedule.
- After exhausting the budget, report and log failure explicitly; do not
  report creation success or send a creation-success email.
- This budget applies to schedule creation, not Bedrock inference, email
  delivery, or the downstream Lambda's execution retries.

## Schedule creation email

- Implement SES email support in `notifications.py`. Keep both the import and
  the call in `handler.py` commented out. No email configuration is read and no
  email service is invoked by the disabled handler integration.
- When enabled, send an email for each successfully created schedule, only
  after AWS confirms creation or reconciliation confirms it succeeded.
- Include the schedule name and ARN, target Lambda name, original description,
  execution date, execution time, and timezone.
- Use these environment variables:

  | Variable | Meaning |
  | --- | --- |
  | `EMAIL_PROVIDER` | Provider selection; initially only `ses` is supported |
  | `EMAIL_SENDER` | SES-verified sender address |
  | `EMAIL_RECIPIENTS` | Nonempty JSON array of recipient addresses |
  | `EMAIL_MAX_ATTEMPTS` | Total send attempts per notification, from 1 to 3 |
  | `EMAIL_RETRY_BASE_SECONDS` | Exponential-backoff base, from 0 to 2 seconds |
  | `EMAIL_RECOVERY_POLICY` | Initially only `manual` is supported |

- Validate configuration when the email function is invoked; unsupported
  providers or policies are errors, never silent fallbacks. SAM exposes these
  settings as parameters mapped to environment variables. Sender/recipient
  values may remain empty while the handler integration is commented out.
- Before enabling email, configure verified SES identities, applicable sandbox
  access, and narrowly scoped `ses:SendEmail` IAM permission. The initial SAM
  role intentionally has no email-sending permission.
- A duplicate delivery of the same job must not cause another replacement or
  notification unless its required notification is still pending. A new job
  replacing the same name has independent notification state.
- If sending fails, preserve the created schedule and surface the notification
  failure separately. Do not recreate the schedule or report that creation
  failed.
- Persist notification attempt counts, pending status, and SES message IDs in
  DynamoDB when the email function is enabled. Under the `manual` recovery
  policy, exhausted or interrupted notifications require operator
  investigation; no automatic recovery worker is implemented.
- Exactly-once email delivery is not assumed. SES success followed by a crash
  before recording its message ID can produce a duplicate on recovery.

## Focused acceptance tests

- Verify Bedrock requests include the description, date response schema,
  precise reference timestamp, input execution time, and timezone; verify valid
  model dates reach Python timestamp validation.
- Verify the description sent to Bedrock ends in `, current date: MM/DD/YYYY`,
  including UTC/local date boundaries and supplied reference timestamps.
- Verify the Bedrock client uses input Region `us-east-1` and invokes input
  model ID `amazon.nova-pro-v1:0`, without changing the Scheduler Region.
- Reject missing or unapproved model/Region inputs without a fallback or
  model invocation.
- Mock ambiguous outcomes, invalid JSON, extra/missing fields, impossible dates,
  refusals, truncation, and service errors; none may create an AWS schedule.
- Verify model output cannot override the target Lambda, time, timezone, or
  invocation role.
- The monthly trigger uses 1:00 AM on the 1st in `America/New_York`.
- A December 1, 2026 invocation resolves the example description to December 11.
- A November 17, 2026 invocation selects December 11 after November's occurrence
  has passed.
- Accept future dates outside the current month, including weekday offsets
  crossing a month or year boundary.
- Instruct Bedrock to advance recurring occurrences that have passed. Python
  rejects returned timestamps at or before the reference without changing them;
  creation retries remain pinned to the original resolved timestamp.
- Instruct Bedrock that equal target/anchor weekdays mean seven days after.
- Reject model-reported ambiguity and target names not ending in `-dynScheduler`.
- Cover returned quarter-end dates, year rollover, leap-day validity, same-day
  future/past boundaries, and explicit model errors. Mocked dates do not prove
  the model correctly interprets these phrases; live tests are separate.
- Verify that the input execution time is preserved in the response and AWS
  schedule expression, including boundary values `00:00:00` and `23:59:59`.
- Reject missing or malformed execution times and out-of-range values such as
  `24:00:00` and `09:60:00`.
- Reject missing or unrecognized timezone identifiers.
- Verify that `America/New_York` uses EST in winter and EDT in summer.
- Verify that the input timezone is preserved in the response and
  `ScheduleExpressionTimezone`, and used for current-date and past-time checks
  near UTC/local date boundaries.
- Verify the exact AWS request, including timezone, target, time window, and
  automatic deletion.
- Reject missing/invalid schedule names and pass valid names unchanged.
- New jobs delete existing same-name schedules before creation, including
  schedules with matching settings. Permanent deletion failure prevents
  creation; a deletion-time not-found race permits creation.
- Duplicate deliveries reuse the same job's verified replacement without
  deleting it. New months/configurations without an explicit ID, or new
  explicit IDs, get independent tokens and retry budgets despite reusing the name.
- Verify new IDs resolve fresh plans and replace the same name, retries with the
  same inputs/ID reuse their original plan even across months, and retries of a
  previously completed request cannot overwrite its newer replacement.
- Verify omitted IDs preserve existing job hashes and reject invalid supplied
  IDs, including empty/nonstring IDs, whitespace, slashes, and excessive length.
- Verify expired plans and lock contention cannot delete an existing schedule.
- Transient creation failures permit exactly three total attempts when all
  fail; success stops retries early, and permanent failures are not retried.
- Reconcile uncertain creation outcomes without duplicate schedules, and
  preserve the logical attempt limit across redelivery.
- Verify the handler never calls SES and reports email as disabled.
- Test the email function independently with mocked SES: include the required
  schedule details, use environment configuration, honor the durable attempt
  budget, and avoid resending a recorded successful notification.
- Email failure preserves the schedule and reports notification failure;
  recovery handles pending notifications without recreating schedules.

## Decisions required before implementation

1. Which additional description patterns, if any, must be supported?
2. Which commercial AWS account and actual target Lambda name should be used?
3. What downstream execution retry policy, dead-letter queues, alarms, and
   Bedrock usage budget are required for production?
4. Before enabling email, supply sender/recipient values and confirm the
   configured send-attempt and manual-recovery settings.

## Initial implementation choices

- Target invocation payload: `{}`.
- Creation retries: three total attempts, with one- and two-second backoff.
  DynamoDB conditional writes and a 150-second lease coordinate duplicate
  invocations; Lambda timeout is 120 seconds.
- SDK calls use one attempt each, a three-second connection timeout, and a
  20-second read timeout. Bedrock failures surface without automatic inference
  retries. Lambda asynchronous execution retries are disabled; the monthly
  trigger may retry delivery twice. Durable creation counts apply across
  deliveries. Interrupted leases require waiting for expiry before recovery.
- Notification code is present but its handler integration is commented out.
  SAM's configurable email examples use SES, three send attempts, one-second
  backoff base, and manual recovery.
- No AWS resources are deployed and no live inference or email calls are made
  during local validation.