# Terraform deployment

This is an alternative to SAM, not an additional deployment step. It creates:

- The Python 3.12 scheduler Lambda and
  `test-schedule-lambda-dynScheduler` test target.
- Four IAM roles and resource-scoped policies for logging, DynamoDB, Bedrock,
  passing the target invocation role, and schedule get/delete/create.
- A pay-per-request encrypted DynamoDB state table.
- Separate groups for generated schedules and the monthly trigger.
- A recurring trigger at 1:00 AM `America/New_York` on the 1st.
- CloudWatch log groups with 14-day retention.
- Scheduler Lambda asynchronous retry configuration (zero retries).

The one-time target schedules are created by the Lambda at runtime, not by
Terraform. The test target receives `{}`. Email integration remains commented
out; environment variables are configured but no SES identity or sending
permission is created. Bedrock is managed by AWS; no model server is deployed.
Model access/agreements must be completed separately.

## Prerequisites

- Terraform 1.7 or later (before 2.0), Python 3.12, and pip.
- AWS credentials for an approved commercial `us-east-1` account with
  deployment permissions for these resources, including IAM.
- Access to Amazon Nova Pro (`amazon.nova-pro-v1:0`) in `us-east-1`.
- The fixed test target name must be available.

Do not manage the same resources through SAM and Terraform. If already
deployed using SAM, plan a deliberate migration/import; do not delete or
overwrite an existing Lambda merely to avoid a name conflict. The fixed test
Lambda name also prevents multiple independent copies in one account/Region.

## Build, initialize, and validate

From the project root:

```bash
# Reuse the project's Python 3.12 virtual environment.
PYTHON="$PWD/.venv/bin/python" bash tf/build.sh
terraform -chdir=tf init
terraform -chdir=tf fmt -check
terraform -chdir=tf validate
terraform -chdir=tf test
```

The build installs the declared dependencies into `tf/.build/scheduler`.
Terraform's archive provider packages that directory and the standalone test
handler. Rebuild before planning/applying changed Python source or dependencies.
Keep the build directory intact between plan and apply.

The Terraform tests use mocked providers and make no AWS calls. Initialization
downloads providers; it does not deploy resources. Commit the generated
`.terraform.lock.hcl` to keep provider selections repeatable.

## Configure and deploy only when authorized

```bash
cp tf/terraform.tfvars.example tf/terraform.tfvars
```

Edit the copied file. All event inputs are assembled from variables; the target
name is wired to the test Lambda created by this deployment. The model/Region
inputs must match the approved Nova Pro / `us-east-1` pair.

Set `monthly_trigger_enabled = false` if you want manual-only tests initially.
The default enables the recurring trigger after deployment.

Using your own AWS profile:

```bash
export AWS_PROFILE=your-test-profile
aws sts get-caller-identity
terraform -chdir=tf plan -out=deployment.tfplan
# Review the account, resource changes, and monthly trigger state first.
terraform -chdir=tf apply deployment.tfplan
```

Applying creates billable AWS resources. No apply is performed by building,
validating, or reading this guide. Never put AWS credentials in Terraform
variables or source files.

## Invoke and inspect

To invoke the scheduler after deployment from the project root:

```bash
terraform -chdir=tf output -raw manual_test_payload > tf/.build/test-event.json
aws lambda invoke \
  --function-name "$(terraform -chdir=tf output -raw scheduler_function_name)" \
  --payload file://tf/.build/test-event.json \
  --cli-binary-format raw-in-base64-out \
  --region us-east-1 \
  tf/.build/scheduler-response.json
```

Inspect the response and Lambda logs for errors; a successful AWS CLI invocation
does not imply the function succeeded. This calls real Bedrock, writes state,
and creates/replaces a real schedule. The same-name schedule in the generated
group may be deleted. Email remains disabled.

To invoke only the harmless test target:

```bash
aws lambda invoke \
  --function-name test-schedule-lambda-dynScheduler \
  --payload '{}' --cli-binary-format raw-in-base64-out \
  --region us-east-1 tf/.build/target-response.json
```

Use the output `schedule_group_name` when inspecting generated schedules and
`state_table_name` when investigating durable attempt state.

## State and cleanup

Terraform uses local state by default. Local state, variable files, plan files,
and build artifacts are ignored by Git. Protect them; state can contain
environment configuration. Use an organization-approved remote backend with
locking before team/production use; this configuration does not provision a
backend bucket or embed backend credentials.

The DynamoDB table has `prevent_destroy = true` to protect retry/idempotency
history, so a normal `terraform destroy` is intentionally blocked. Review
backup/retention and explicitly approve any lifecycle change before destroying
or replacing it. This is a guard, not an AWS deletion-proof retention policy.

Stop the monthly trigger before teardown. Review any runtime-created schedules
in the generated group: removing a Scheduler group can remove its schedules.
Never destroy shared or imported resources without an approved migration and
cleanup plan.
