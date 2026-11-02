from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct
from deploy_time_build import ContainerImageBuild

from .helpers import configure_stack

ENDPOINT = "probe"


class RuntimeStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env: cdk.Environment,
        stack_name: str,
        runtime_name: str,
        image_directory: Path,
    ) -> None:
        super().__init__(scope, construct_id, env=env, stack_name=stack_name)
        # CodeBuild builds the ARM64 image during `cdk deploy`; no local Docker is needed.
        # Only the Dockerfile and its download helper are uploaded as build context.
        image = ContainerImageBuild(
            self,
            "DeciderImage",
            directory=str(image_directory),
            file="Dockerfile.decider-test",
            platform=ecr_assets.Platform.LINUX_ARM64,
            exclude=["*", "!Dockerfile.decider-test", "!runtime_downloads.py"],
            ignore_mode=cdk.IgnoreMode.DOCKER,
        )
        role = iam.CfnRole(
            self,
            "RuntimeRole",
            assume_role_policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                        "Condition": {
                            "StringEquals": {"aws:SourceAccount": cdk.Aws.ACCOUNT_ID},
                            "ArnLike": {
                                "aws:SourceArn": cdk.Fn.sub(
                                    "arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:runtime/"
                                    + runtime_name
                                    + "-*"
                                )
                            },
                        },
                    }
                ],
            },
            policies=[
                iam.CfnRole.PolicyProperty(
                    policy_name="ReadImageAndWriteLogs",
                    policy_document={
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "ecr:GetAuthorizationToken",
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                                "Resource": image.repository.repository_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:DescribeLogStreams",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": cdk.Fn.sub(
                                    "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/bedrock-agentcore/runtimes/"
                                    + runtime_name
                                    + "-*"
                                ),
                            },
                        ],
                    },
                )
            ],
        )
        runtime = agentcore.CfnRuntime(
            self,
            "Runtime",
            agent_runtime_name=runtime_name,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri
                )
            ),
            role_arn=role.attr_arn,
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            protocol_configuration="HTTP",
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=60, max_lifetime=1800
            ),
            environment_variables={
                "DECIDER_MODEL": "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610",
                "DECIDER_REVISION": "216d1ccb46a9e9b036eb9187b248db98480f77d4",
                "DECIDER_QUANT": "int8",
                "DECIDER_MAX_TOKENS": "2048",
                "DECIDER_MAX_QUESTIONS": "8",
                "DECIDER_MAX_BATCH": "1",
                "DECIDER_THREADS": "2",
                "DECIDER_WARMUP": "1",
                "DECIDER_LOAD_TIMEOUT_S": "600",
            },
        )
        # The image must be pushed before AgentCore validates the container URI.
        runtime.node.add_dependency(image)
        default_logs = logs.CfnLogGroup(
            self,
            "DefaultLogs",
            log_group_name=cdk.Fn.sub(
                "/aws/bedrock-agentcore/runtimes/${Runtime.AgentRuntimeId}-DEFAULT"
            ),
            retention_in_days=3,
        )
        endpoint_logs = logs.CfnLogGroup(
            self,
            "EndpointLogs",
            log_group_name=cdk.Fn.sub(
                "/aws/bedrock-agentcore/runtimes/${Runtime.AgentRuntimeId}-" + ENDPOINT
            ),
            retention_in_days=3,
        )
        endpoint = agentcore.CfnRuntimeEndpoint(
            self,
            "Endpoint",
            agent_runtime_id=runtime.attr_agent_runtime_id,
            agent_runtime_version=runtime.attr_agent_runtime_version,
            name=ENDPOINT,
        )
        endpoint.add_resource_dependency(default_logs)
        endpoint.add_resource_dependency(endpoint_logs)
        self.runtime_arn = runtime.attr_agent_runtime_arn
        self.runtime_name = runtime_name
        cdk.CfnOutput(self, "RuntimeArn", value=runtime.attr_agent_runtime_arn)
        cdk.CfnOutput(self, "RuntimeId", value=runtime.attr_agent_runtime_id)
        cdk.CfnOutput(self, "RuntimeQualifier", value=ENDPOINT)
        cdk.CfnOutput(self, "ImageUri", value=image.image_uri)
        configure_stack(
            self,
            "Nevermind Decider model: deploy-time ARM64 image build and IAM-authenticated HTTP AgentCore runtime.",
        )
