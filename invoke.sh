#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s <filename.json|events/filename.json>\n' "$0" >&2
  exit 1
fi

filename="${1#events/}"
if [[ "$filename" == */* || "$filename" != *.json || ! -f "$project_dir/events/$filename" ]]; then
  printf 'Event JSON file not found in events/: %s\n' "$1" >&2
  exit 1
fi

function_error="$(aws lambda invoke \
  --function-name dynamic-lambda-scheduler-scheduler \
  --payload "file://$project_dir/events/$filename" \
  --cli-binary-format raw-in-base64-out \
  --query FunctionError \
  --output text \
  "$project_dir/response.json")"

cat "$project_dir/response.json"
printf '\n'

if [[ -n "$function_error" && "$function_error" != None ]]; then
  printf 'Lambda invocation failed: %s\n' "$function_error" >&2
  exit 1
fi
