import aws_cdk as cdk
from cdk_nag import NagPackSuppression, NagSuppressions


def existing_demo_exceptions(build: cdk.Stack, runtime: cdk.Stack, app: cdk.Stack) -> None:
    NagSuppressions.add_resource_suppressions(
        build.node.find_child("BuildRole"),
        [
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="CodeBuild reads only this bucket's input prefix. ECR authorization tokens do not support resource-level permissions; image push permissions remain repository-scoped.",
                applies_to=["Resource::<Sources.Arn>/input/*", "Resource::*"],
            )
        ],
    )
    NagSuppressions.add_resource_suppressions(
        runtime.node.find_child("RuntimeRole"),
        [
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="ECR authorization requires Resource *. Logs are scoped to this runtime name's generated session log streams; the role has no model-generation or data-mutation permission.",
                applies_to=[
                    "Resource::*",
                    "Resource::arn:<AWS::Partition>:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/<RuntimeName>-*",
                ],
            )
        ],
    )
    NagSuppressions.add_resource_suppressions(
        build.node.find_child("Build"),
        [
            NagPackSuppression(
                id="AwsSolutions-CB4",
                reason="This project produces NO_ARTIFACTS. Its private source bucket is encrypted, logs use a customer-managed KMS key, and output images use ECR encryption.",
            )
        ],
    )
    for stack, name in [(build, "Sources"), (app, "WebFiles")]:
        NagSuppressions.add_resource_suppressions(
            stack.node.find_child(name),
            [
                NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason="Synthetic, invite-only demo retains the existing no-S3-access-logs configuration during migration. Do not treat this as a production compliance baseline.",
                )
            ],
            apply_to_children=True,
        )
    NagSuppressions.add_resource_suppressions(
        app.node.find_child("WorkerRole"),
        [
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="The selected US Nova inference profile requires the same model ARN in its US destination regions; no other model is authorized.",
                applies_to=[
                    "Resource::arn:<AWS::Partition>:bedrock:us-*::foundation-model/amazon.nova-2-lite-v1:0"
                ],
            )
        ],
    )
    NagSuppressions.add_resource_suppressions(
        app.node.find_child("Users"),
        [
            NagPackSuppression(
                id="AwsSolutions-COG8",
                reason="Preserve the existing Essentials plan. A migration must not silently upgrade this experimental account to a more expensive Cognito plan.",
            ),
            NagPackSuppression(
                id="AwsSolutions-COG2",
                reason="Existing invited users have optional TOTP MFA. Preserve their sign-in behavior during the infrastructure-only migration; public signup stays disabled.",
            ),
        ],
    )
    NagSuppressions.add_resource_suppressions(
        app.node.find_child("Website"),
        [
            NagPackSuppression(
                id="AwsSolutions-CFR1",
                reason="This public static demo has no country restriction requirement; the API still requires a Cognito access token.",
            ),
            NagPackSuppression(
                id="AwsSolutions-CFR2",
                reason="No WAF is provisioned for the existing small demo. Keep authenticated API access, zero API caching, and application request/model limits unchanged.",
            ),
            NagPackSuppression(
                id="AwsSolutions-CFR3",
                reason="Retain the demo's existing no-CloudFront-access-logs configuration. Authenticated API access logs are enabled and KMS-encrypted.",
            ),
            NagPackSuppression(
                id="AwsSolutions-CFR4",
                reason="Preserve the existing default CloudFront hostname and certificate. A configurable modern minimum viewer TLS policy requires a custom hostname/certificate, outside this migration.",
            ),
        ],
    )
    for name in ["ApiFunction", "WorkerFunction"]:
        NagSuppressions.add_resource_suppressions(
            app.node.find_child(name),
            [
                NagPackSuppression(
                    id="AwsSolutions-L1",
                    reason="Python 3.12 is the application's tested, hash-locked runtime. Upgrading application dependencies/runtime is separate from preserving the deployed stack.",
                )
            ],
        )
