#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${AWS_PROFILE:?Set AWS_PROFILE to the approved project profile}"
: "${NEVERMIND_RUNTIME_ARN:?Set the tested AgentCore runtime ARN}"
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
stack="${NEVERMIND_STACK_NAME:-nevermind-app}"
account=$(aws sts get-caller-identity --query Account --output text)
bucket="$stack-artifacts-$account-$AWS_REGION"
mkdir -p .local/deploy
aws s3api list-buckets --query "Buckets[?Name=='$bucket'].Name" --output text >.local/deploy/artifact-bucket-check
if [[ ! -s .local/deploy/artifact-bucket-check ]]; then
  aws s3api create-bucket --bucket "$bucket" --region us-east-1
  aws s3api put-public-access-block --bucket "$bucket" --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$bucket" --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-bucket-versioning --bucket "$bucket" --versioning-configuration Status=Enabled
  aws s3api put-bucket-policy --bucket "$bucket" --policy "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":\"s3:*\",\"Resource\":[\"arn:aws:s3:::$bucket\",\"arn:aws:s3:::$bucket/*\"],\"Condition\":{\"Bool\":{\"aws:SecureTransport\":\"false\"}}}]}"
fi
archive=$(cat .local/package-path)
digest=$(shasum -a 256 "$archive" | cut -d ' ' -f1)
key="lambda/$digest.zip"
aws s3 cp "$archive" "s3://$bucket/$key" --sse AES256 --only-show-errors
.venv/bin/cfn-lint --regions "$AWS_REGION" --template deploy/app.yaml
type=CREATE
if aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE --query "StackSummaries[?StackName=='$stack'].StackName" --output text | grep -q "$stack"; then
  type=UPDATE
fi
change="app-$(date +%s)"
aws cloudformation create-change-set --stack-name "$stack" --change-set-name "$change" \
  --change-set-type "$type" --template-body file://deploy/app.yaml --capabilities CAPABILITY_IAM \
  --parameters "ParameterKey=CodeBucket,ParameterValue=$bucket" "ParameterKey=CodeKey,ParameterValue=$key" \
    "ParameterKey=RuntimeArn,ParameterValue=$NEVERMIND_RUNTIME_ARN" "ParameterKey=RuntimeQualifier,ParameterValue=probe"
aws cloudformation wait change-set-create-complete --stack-name "$stack" --change-set-name "$change"
aws cloudformation describe-events --stack-name "$stack" --change-set-name "$change" >.local/deploy/preflight.json
aws cloudformation describe-change-set --stack-name "$stack" --change-set-name "$change" \
  --query 'Changes[].ResourceChange.{Action:Action,Type:ResourceType,Name:LogicalResourceId}' --output table
aws cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change"
if [[ "$type" == CREATE ]]; then
  aws cloudformation wait stack-create-complete --stack-name "$stack"
else
  aws cloudformation wait stack-update-complete --stack-name "$stack"
fi
aws cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].Outputs' --output json >.local/deploy/app-outputs.json
.venv/bin/python deploy/web_config.py
web_bucket=$(aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Outputs[?OutputKey=='WebBucket'].OutputValue | [0]" --output text)
distribution=$(aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue | [0]" --output text)
aws s3 sync frontend/dist/ "s3://$web_bucket/" --only-show-errors --cache-control 'public,max-age=31536000,immutable'
aws s3 cp frontend/dist/index.html "s3://$web_bucket/index.html" --only-show-errors --cache-control no-cache
aws s3 cp .local/deploy/config.json "s3://$web_bucket/config.json" --only-show-errors --content-type application/json --cache-control no-store
aws cloudfront create-invalidation --distribution-id "$distribution" --paths /index.html /config.json --query Invalidation.Id --output text
aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Outputs[?OutputKey=='WebsiteUrl'].OutputValue | [0]" --output text
