# Python CDK infrastructure

The three stacks are native Python CDK code. The earlier standalone deployment templates are no longer a second source of truth.

| CDK stack ID | Default CloudFormation stack name | Responsibility |
|---|---|---|
| `Build` | `nevermind-model-build` | Private versioned build and Lambda artifact bucket, immutable model images, bounded ARM64 CodeBuild, encrypted logs |
| `Runtime` | `nevermind-model` | IAM-authenticated HTTP Decider runtime and `probe` endpoint |
| `Application` | `nevermind-app` | Invite-only website/API, Lambda worker, queues, DynamoDB workshop state |

`Runtime` depends on `Build`, and `Application` depends on `Runtime`. Runtime/image values pass through explicit deployment parameters rather than permanent CloudFormation exports.

## Setup

Run the root README's Python setup first, then from the repository root:

```bash
make cdk-install
cd frontend && npm ci && cd ..
```

The infrastructure Python dependencies and CLI have separate lockfiles. The CLI uses a workspace-local Node 22 binary; it does not change the developer's global Node installation.

```bash
make cdk-test
make cdk-synth
export AWS_PROFILE=your-project-profile
export AWS_REGION=us-east-1
make cdk-diff
make cdk-deploy
make cdk-outputs
make cdk-drift
```

For a new account/region, review and run `make cdk-bootstrap` once. Bootstrap creates shared deployment roles and storage and needs elevated permissions. Do not delete or silently reconfigure an existing shared `CDKToolkit` stack.

`NEVERMIND_PROJECT` defaults to `nevermind`. Set it before all commands to deploy an independent environment. The supported region is currently `us-east-1`. The old independent stack-name/runtime-name overrides are rejected rather than silently mixing environments.

## Deployment and updates

`make cdk-deploy` performs the complete sequence:

1. Prepare the CDK build-stack change set.
2. Refuse any proposed resource removal or possible replacement before executing it.
3. Package only the three reviewed model build inputs. Reuse an existing immutable image with that source hash, or build and push it on ARM64 CodeBuild.
4. Deploy the runtime by image digest and confirm its endpoint is ready.
5. Package hash-locked Lambda dependencies and application code. Store this archive in the managed build bucket, not an unmanaged artifact bucket.
6. Deploy the application stack through the same change-set check.
7. Build and upload the website and public login configuration, then invalidate the HTML/config cache.

Failed commands stop the sequence with an error. Re-running can resume from successfully deployed stacks and reuses an existing model image. It does not repeat workshop approvals or model-driven effects.

The check requests AWS-evaluated property values; unresolved summary information can otherwise report conditional replacements for metadata-only changes. CDK prepares the change set, and the standard CloudFormation API executes that exact checked change set. This avoids a pinned CDK CLI rendering error on metadata-only execution without bypassing the replacement check.

For a runtime-only update, use `make -C infra runtime`. The earlier `deploy/runtime.sh` and `deploy/deploy.sh` commands forward to CDK; they no longer deploy independent YAML templates.

Outputs, generated templates, asset copies, and change-set records stay under ignored `.local/`. `deploy/create_invite.py` consumes the latest application's output file, as before. No AWS credentials enter browser code or synthesis context.

## Disposable destruction

```bash
make cdk-destroy
```

This prints the target account and project and requires typing `DELETE <account>/<project>` exactly. A missing or incorrect confirmation stops before calling CDK destruction.

Confirmed destruction removes the three stacks and their workshop state, invited user accounts, messages, logs, website files, build inputs, and repository images. CDK's bucket-cleanup provider removes all object versions before bucket deletion. ECR is configured to empty images on deletion. The wrapper also deletes the cleanup providers' own log groups, using physical function IDs recorded from these stacks before destruction, never an account-wide name search. Those provider logs have seven-day retention while deployed. Encryption keys enter AWS's seven-day deletion waiting period; this is not immediate physical removal.

Creating the app again starts with empty state and new login accounts. The website address may change. It does not restore old data. Keep any desired export before confirming destruction.

The shared CDK bootstrap is intentionally outside this operation. Legacy resources created before the managed stacks—such as the original experimental runtime, repository, log key, and old application artifact bucket—also require separate reviewed cleanup. No account-wide discovery/deletion or cleanup of other projects is performed.

## Migration and checks

Existing logical IDs, stack names, and CloudFormation expressions are deliberately preserved. Resource-level CDK constructs are used where higher-level defaults would change the existing deployment. Higher-level S3 constructs supply automatic cleanup; their resource and policy logical IDs are explicitly preserved.

Tests assert stable logical IDs, protected API/auth settings, model limits, encrypted logs, disposable storage settings, and refusal of unsafe deployment/destruction. `cdk-nag` runs during strict synthesis. Resource-specific exceptions in `nevermind_infra/compliance.py` explain existing demonstration limitations rather than silently changing the Cognito plan, runtime version, website hostname, or logging behavior.

CloudFormation schema linting and Guard can also inspect the synthesized templates. `.cfnlintrc` ignores only W3005, the redundant-dependency style rule triggered by CDK's built-in cleanup provider.

A CDK diff is not permission to delete or replace data. Deployment still reviews the actual prepared change set. A live destroy/recreate cycle requires separate approval; an in-place migration alone does not establish that it has been tested.

The existing deployment was migrated in place and all original physical IDs and workshop records were verified unchanged. Post-migration hosted sign-in/API access and signed Decider inference succeeded. The final template diff was empty, and live drift detection reported zero drift with nine unsupported resources unchecked. No live destruction was performed.
