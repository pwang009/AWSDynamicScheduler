# Agent instructions

## Project goal

Build and maintain an AWS Lambda application for this project. Follow the
requirements in the root-level `REQUIREMENTS.md` file when one is present.

## Working agreements

- Inspect the existing project and its dependencies before choosing a runtime,
  framework, or project structure.
- Do not guess about behavior, integrations, permissions, or deployment choices
  that are not specified. Ask for clarification when a decision would
  materially affect the implementation.
- Keep IAM permissions scoped to the actions and resources the Lambda needs.
- Keep secrets out of source code; use an appropriate AWS-managed secret or
  configuration mechanism.
- Add or update focused tests for behavior you change, and run the relevant
  tests and checks before finishing.
- Update project documentation when implementation or operation changes.
- Do not deploy, modify AWS resources, or incur cloud costs unless explicitly
  asked to do so.

## Requirements

Put the Lambda's functional and non-functional requirements in
`REQUIREMENTS.md` at the project root, next to this file. Include expected
inputs and outputs, trigger/source, behavior, error handling, security or
permission needs, and deployment/runtime constraints when known. Mark unknown
items as undecided rather than assuming an answer.
