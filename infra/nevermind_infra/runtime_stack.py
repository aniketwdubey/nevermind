import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from .helpers import configure_stack


class RuntimeStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, env: cdk.Environment, stack_name: str
    ) -> None:
        super().__init__(scope, construct_id, env=env, stack_name=stack_name)
        name = cdk.CfnParameter(
            self,
            "RuntimeName",
            type="String",
            default="nevermind_decider",
            allowed_pattern="[A-Za-z][A-Za-z0-9_]{0,47}",
        )
        image = cdk.CfnParameter(
            self,
            "ImageUri",
            type="String",
            allowed_pattern="[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[a-z0-9/_.-]+@sha256:[a-f0-9]{64}",
        )
        repository = cdk.CfnParameter(
            self,
            "RepositoryArn",
            type="String",
            allowed_pattern="arn:aws:ecr:[a-z0-9-]+:[0-9]{12}:repository/[a-z0-9/_.-]+",
        )
        log_key = cdk.CfnParameter(
            self,
            "LogKeyArn",
            type="String",
            allowed_pattern="arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[a-f0-9-]+",
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
                                    "arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:runtime/${RuntimeName}-*"
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
                                "Resource": repository.value_as_string,
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:DescribeLogStreams",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": cdk.Fn.sub(
                                    "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/bedrock-agentcore/runtimes/${RuntimeName}-*"
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
            agent_runtime_name=name.value_as_string,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.value_as_string
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
        default_logs = logs.CfnLogGroup(
            self,
            "DefaultLogs",
            log_group_name=cdk.Fn.sub(
                "/aws/bedrock-agentcore/runtimes/${Runtime.AgentRuntimeId}-DEFAULT"
            ),
            kms_key_id=log_key.value_as_string,
            retention_in_days=3,
        )
        endpoint_logs = logs.CfnLogGroup(
            self,
            "EndpointLogs",
            log_group_name=cdk.Fn.sub(
                "/aws/bedrock-agentcore/runtimes/${Runtime.AgentRuntimeId}-probe"
            ),
            kms_key_id=log_key.value_as_string,
            retention_in_days=3,
        )
        endpoint = agentcore.CfnRuntimeEndpoint(
            self,
            "Endpoint",
            agent_runtime_id=runtime.attr_agent_runtime_id,
            agent_runtime_version=runtime.attr_agent_runtime_version,
            name="probe",
        )
        endpoint.add_resource_dependency(default_logs)
        endpoint.add_resource_dependency(endpoint_logs)
        cdk.CfnOutput(self, "RuntimeArn", value=runtime.attr_agent_runtime_arn)
        cdk.CfnOutput(self, "RuntimeId", value=runtime.attr_agent_runtime_id)
        cdk.CfnOutput(self, "RuntimeQualifier", value="probe")
        configure_stack(
            self,
            "IAM-authenticated HTTP Decider runtime for Nevermind; synthetic demonstration only, no anonymous inference.",
        )
