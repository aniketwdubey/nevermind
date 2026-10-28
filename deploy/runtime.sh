#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${AWS_PROFILE:?Set AWS_PROFILE to the approved project profile}"
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
if [[ "$AWS_REGION" != us-east-1 ]]; then
  echo "This tested demonstration currently supports us-east-1 only." >&2
  exit 1
fi
stack="${NEVERMIND_RUNTIME_STACK:-nevermind-model}"
build_stack="$stack-build"
name="${NEVERMIND_RUNTIME_NAME:-nevermind_decider}"
directory=".local/runtime"
mkdir -p "$directory"
.venv/bin/cfn-lint --regions "$AWS_REGION" --template deploy/runtime-build.yaml deploy/runtime.yaml
cfn-guard validate --rules deploy/security.guard --data deploy/runtime-build.yaml --output-format json >"$directory/build-guard.json"
cfn-guard validate --rules deploy/security.guard --data deploy/runtime.yaml --output-format json >"$directory/runtime-guard.json"
aws sts get-caller-identity --query Account --output text

deploy_stack() {
  local target="$1" template="$2" type=CREATE change id reason
  shift 2
  if aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE \
    --query "StackSummaries[?StackName=='$target'].StackName" --output text | grep -Fxq "$target"; then
    type=UPDATE
  fi
  change="runtime-$(date +%s)"
  id=$(aws cloudformation create-change-set --stack-name "$target" --change-set-name "$change" \
    --change-set-type "$type" --template-body "file://$template" --capabilities CAPABILITY_IAM \
    "$@" --query Id --output text)
  if ! aws cloudformation wait change-set-create-complete --change-set-name "$id" >"$directory/$target-wait.log" 2>&1; then
    reason=$(aws cloudformation describe-change-set --change-set-name "$id" --query StatusReason --output text)
    if [[ "$type" == UPDATE && "$reason" == *"didn't contain changes"* ]]; then
      echo "No infrastructure changes for $target."
      return
    fi
    aws cloudformation describe-events --change-set-name "$id" >"$directory/$target-events.json"
    cat "$directory/$target-wait.log" >&2
    echo "Change set failed: $reason. See $directory/$target-events.json" >&2
    return 1
  fi
  aws cloudformation describe-events --change-set-name "$id" >"$directory/$target-events.json"
  aws cloudformation describe-change-set --change-set-name "$id" >"$directory/$target-changes.json"
  .venv/bin/python - "$directory/$target-changes.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1]))
for item in data["Changes"]:
    resource = item["ResourceChange"]
    print(resource["Action"], resource["LogicalResourceId"], resource.get("Replacement", ""))
    if resource["Action"] == "Remove" or resource.get("Replacement") in {"True", "Conditional"}:
        raise SystemExit("Refusing deletion or replacement. Review this change set manually.")
PY
  aws cloudformation execute-change-set --change-set-name "$id"
  if [[ "$type" == CREATE ]]; then
    aws cloudformation wait stack-create-complete --stack-name "$target"
  else
    aws cloudformation wait stack-update-complete --stack-name "$target"
  fi
}

output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text
}

deploy_stack "$build_stack" deploy/runtime-build.yaml
bucket=$(output "$build_stack" SourceBucket)
repository=$(output "$build_stack" RepositoryUri)
repository_name=$(output "$build_stack" RepositoryName)
project=$(output "$build_stack" BuildProject)
hash=$(.venv/bin/python deploy/runtime_source.py "$directory/source.zip")
tag="source-$hash"
image="$repository:$tag"
if aws ecr list-images --repository-name "$repository_name" \
  --query "imageIds[?imageTag=='$tag'].imageTag" --output text | grep -Fxq "$tag"; then
  echo "Reusing previously built immutable image."
else
  version=$(aws s3api put-object --bucket "$bucket" --key input/source.zip \
    --body "$directory/source.zip" --server-side-encryption AES256 --query VersionId --output text)
  build=$(aws codebuild start-build --project-name "$project" --source-version "$version" \
    --environment-variables-override \
      "name=REGISTRY,value=${repository%%/*},type=PLAINTEXT" \
      "name=IMAGE_URI,value=$image,type=PLAINTEXT" --query build.id --output text)
  printf '%s\n' "$build" >"$directory/build-id"
  status=IN_PROGRESS
  for ((attempt=0; attempt<125; attempt++)); do
    status=$(aws codebuild batch-get-builds --ids "$build" --query 'builds[0].buildStatus' --output text)
    echo "Image build: $status"
    [[ "$status" == IN_PROGRESS ]] || break
    sleep 15
  done
  if [[ "$status" != SUCCEEDED ]]; then
    aws codebuild batch-get-builds --ids "$build" >"$directory/build-result.json"
    if [[ "$status" == IN_PROGRESS ]]; then
      aws codebuild stop-build --id "$build" >"$directory/build-stop.json"
    fi
    echo "Image build did not succeed. See $directory/build-result.json" >&2
    exit 1
  fi
fi
digest=$(aws ecr describe-images --repository-name "$repository_name" --image-ids "imageTag=$tag" \
  --query 'imageDetails[0].imageDigest' --output text)
deploy_stack "$stack" deploy/runtime.yaml --parameters \
  "ParameterKey=RuntimeName,ParameterValue=$name" \
  "ParameterKey=ImageUri,ParameterValue=$repository@$digest" \
  "ParameterKey=RepositoryArn,ParameterValue=$(output "$build_stack" RepositoryArn)" \
  "ParameterKey=LogKeyArn,ParameterValue=$(output "$build_stack" LogKeyArn)"
aws cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].Outputs' >"$directory/outputs.json"
runtime=$(output "$stack" RuntimeId)
expected_version=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$runtime" \
  --query agentRuntimeVersion --output text)
aws bedrock-agentcore-control get-agent-runtime-endpoint --agent-runtime-id "$runtime" --endpoint-name probe \
  --output json >"$directory/endpoint.json"
.venv/bin/python - "$directory/endpoint.json" "$expected_version" <<'PY'
import json
import sys
with open(sys.argv[1]) as file:
    endpoint = json.load(file)
if endpoint["status"] != "READY" or endpoint["liveVersion"] != sys.argv[2]:
    raise SystemExit("Runtime endpoint is not ready on the target version.")
print("Runtime endpoint READY on version", endpoint["liveVersion"])
PY
echo "Runtime deployed. Verify real inference with examples/check_change.py before connecting the app:"
output "$stack" RuntimeArn
