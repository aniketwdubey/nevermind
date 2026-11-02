# Python CDK infrastructure

Nevermind is deployed with plain `cdk deploy`. There is no custom deployment script.

| CDK stack ID | CloudFormation stack | Contents |
|---|---|---|
| `Runtime` | `nevermind-model` | Decider image build (AWS CodeBuild, ARM64), private image repository, AgentCore runtime and `probe` endpoint |
| `Application` | `nevermind-app` | CloudFront website, Cognito login, HTTP API, Lambda API and worker, SQS queues, DynamoDB state |

`Application` reads the runtime ARN from `Runtime` through a CloudFormation export, so CDK deploys `Runtime` first. The runtime ARN stays the same when a new image is deployed; the runtime is updated in place.

## Commands

From the repository root, after the Python setup in the main README:

```bash
make cdk-install                      # locked CDK libraries, CLI and Node 22
cd frontend && npm ci && cd ..
make cdk-test                         # offline template tests

export AWS_PROFILE=your-project-profile
export AWS_REGION=us-east-1
make cdk-bootstrap                    # once per account/region (shared by all CDK apps)
make cdk-diff                         # preview changes
make cdk-deploy                       # build and deploy everything
make cdk-outputs                      # website and login details
make cdk-drift                        # compare live resources with templates
make cdk-destroy                      # delete both stacks and their data
```

`make cdk-deploy` runs `deploy/package.sh` (hash-locked Lambda dependencies into `.local/lambda-package`), builds the website into `frontend/dist`, then runs `cdk deploy --all`. Both directories must exist for synthesis.

`NEVERMIND_PROJECT` (default `nevermind`) names both stacks and the runtime; set it consistently for an independent environment.

## How the pieces are built

- **Model image:** the [deploy-time-build](https://github.com/tmokmss/deploy-time-build) `ContainerImageBuild` construct (MIT, pinned in `requirements.in`) uploads only `deploy/Dockerfile.decider-test` and `deploy/runtime_downloads.py`, runs an ARM64 CodeBuild job during the deployment, and returns the image URI. The runtime depends on it, so AgentCore always receives a pushed image. No local Docker is required.
- **Lambda code:** a normal CDK file asset uploaded to the bootstrap bucket. It only changes when the packaged files change.
- **Website:** two CDK `BucketDeployment`s. Fingerprinted files under `assets/` are cached for a year; `index.html` and the generated `config.json` (public client ID and login URLs) are revalidated on every request and invalidated in CloudFront after each deployment.

## Encryption and logs

There are no customer-managed KMS keys. S3, DynamoDB, SQS and CloudWatch Logs use AWS default encryption at rest. Application and runtime log groups expire after three or seven days.

## Safety and checks

- Existing logical resource IDs and name expressions are kept stable and covered by tests, because changing them would replace live resources.
- `cdk diff` shows replacements before deployment. Review it whenever the deployment holds data you care about; `make cdk-deploy` does not stop for approval.
- `cdk-nag` runs in strict synthesis. `nevermind_infra/compliance.py` documents each accepted finding, including findings in CDK and deploy-time-build helper resources.
- `.cfnlintrc` ignores only W3005, a redundant-dependency style warning in CDK's bucket cleanup helper.

## Destruction

`make cdk-destroy` deletes both stacks. Everything is disposable: the website bucket is emptied automatically, the image repository is emptied on deletion, and the workshop table and user pool are deleted. Redeploying starts with empty data, new login accounts and possibly a new website address.

The shared CDK bootstrap is not deleted. A few small log groups that AWS creates automatically for deployment helpers (Lambda functions and the CodeBuild project) can remain after destruction; delete them manually if wanted.
