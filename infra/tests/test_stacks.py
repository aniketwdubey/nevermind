import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Match, Template
from cdk_nag import AwsSolutionsChecks
from nevermind_infra.app_stack import ApplicationStack
from nevermind_infra.build_stack import BuildStack
from nevermind_infra.compliance import existing_demo_exceptions
from nevermind_infra.runtime_stack import RuntimeStack

ORIGINAL_IDS = {
    "Application": {
        "ApiFunction",
        "ApiLog",
        "ApiPermission",
        "ApiRole",
        "ApiRoute",
        "ApiStage",
        "Authorizer",
        "BrowserClient",
        "FailedJobs",
        "FailedQueuePolicy",
        "HttpApi",
        "HttpLog",
        "Integration",
        "Jobs",
        "LogKey",
        "LoginBranding",
        "LoginDomain",
        "NoApiCache",
        "QueuePolicy",
        "State",
        "Users",
        "WebAccess",
        "WebFiles",
        "WebHeaders",
        "WebPolicy",
        "Website",
        "WorkerFunction",
        "WorkerLog",
        "WorkerRole",
        "WorkerSource",
    },
    "Build": {"Build", "BuildLogs", "BuildRole", "Images", "LogKey", "Sources", "SourcesPolicy"},
    "Runtime": {"DefaultLogs", "Endpoint", "EndpointLogs", "Runtime", "RuntimeRole"},
}


@pytest.fixture(scope="module")
def templates() -> dict[str, Template]:
    app = cdk.App()
    environment = cdk.Environment(account="123456789012", region="us-east-1")
    stacks = {
        "Build": BuildStack(app, "Build", stack_name="example-model-build", env=environment),
        "Runtime": RuntimeStack(app, "Runtime", stack_name="example-model", env=environment),
        "Application": ApplicationStack(
            app, "Application", stack_name="example-app", env=environment
        ),
    }
    existing_demo_exceptions(stacks["Build"], stacks["Runtime"], stacks["Application"])
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    result = {name: Template.from_stack(stack) for name, stack in stacks.items()}
    for stack in stacks.values():
        Annotations.from_stack(stack).has_no_error("*", Match.any_value())
        Annotations.from_stack(stack).has_no_warning("*", Match.any_value())
    return result


def test_original_logical_ids_and_resource_types_remain_stable(
    templates: dict[str, Template],
) -> None:
    for name, original in ORIGINAL_IDS.items():
        resources = templates[name].to_json()["Resources"]
        assert original <= resources.keys()
        for resource in original:
            assert resources[resource]["DeletionPolicy"] == "Delete"
    application = templates["Application"].to_json()["Resources"]
    assert application["State"]["Type"] == "AWS::DynamoDB::Table"
    assert application["Users"]["Type"] == "AWS::Cognito::UserPool"
    assert application["WebFiles"]["Type"] == "AWS::S3::Bucket"
    assert (
        templates["Runtime"].to_json()["Resources"]["Runtime"]["Type"]
        == "AWS::BedrockAgentCore::Runtime"
    )


def test_disposable_buckets_empty_versions_and_repository_images(
    templates: dict[str, Template],
) -> None:
    for name, bucket in [("Build", "Sources"), ("Application", "WebFiles")]:
        template = templates[name]
        template.resource_count_is("Custom::S3AutoDeleteObjects", 1)
        template.has_resource_properties(
            "AWS::S3::Bucket",
            {
                "VersioningConfiguration": {"Status": "Enabled"},
                "Tags": [{"Key": "aws-cdk:auto-delete-objects", "Value": "true"}],
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            },
        )
        assert template.to_json()["Resources"][bucket]["DeletionPolicy"] == "Delete"
    templates["Build"].has_resource_properties(
        "AWS::ECR::Repository",
        {
            "EmptyOnDelete": True,
            "ImageTagMutability": "IMMUTABLE",
        },
    )
    for name in ["Build", "Application"]:
        templates[name].has_resource_properties("AWS::KMS::Key", {"PendingWindowInDays": 7})


def test_application_auth_cache_and_transactional_state_are_unchanged(
    templates: dict[str, Template],
) -> None:
    application = templates["Application"]
    application.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {
            "AuthorizationType": "JWT",
            "AuthorizationScopes": ["openid"],
        },
    )
    application.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "AccessLogSettings": Match.object_like(
                {
                    "DestinationArn": {
                        "Fn::Sub": "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${HttpLog}",
                    },
                }
            ),
        },
    )
    application.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
            "UserPoolTier": "ESSENTIALS",
        },
    )
    application.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "GenerateSecret": False,
            "AllowedOAuthFlows": ["code"],
            "ExplicitAuthFlows": ["ALLOW_USER_SRP_AUTH", "ALLOW_ADMIN_USER_PASSWORD_AUTH"],
        },
    )
    application.has_resource_properties("AWS::Cognito::UserPoolDomain", {"ManagedLoginVersion": 2})
    application.resource_count_is("AWS::Cognito::ManagedLoginBranding", 1)
    application.has_resource_properties(
        "AWS::CloudFront::CachePolicy",
        {
            "CachePolicyConfig": Match.object_like({"MinTTL": 0, "DefaultTTL": 0, "MaxTTL": 0}),
        },
    )
    application.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "SSESpecification": {"SSEEnabled": True},
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        },
    )


def test_runtime_limits_and_iam_authentication_are_preserved(
    templates: dict[str, Template],
) -> None:
    runtime = templates["Runtime"].to_json()["Resources"]["Runtime"]["Properties"]
    assert "AuthorizerConfiguration" not in runtime  # AgentCore's default is IAM, not anonymous.
    assert runtime["ProtocolConfiguration"] == "HTTP"
    assert runtime["LifecycleConfiguration"] == {
        "IdleRuntimeSessionTimeout": 60,
        "MaxLifetime": 1800,
    }
    assert runtime["EnvironmentVariables"]["DECIDER_MAX_TOKENS"] == "2048"
    assert (
        runtime["EnvironmentVariables"]["DECIDER_REVISION"]
        == "216d1ccb46a9e9b036eb9187b248db98480f77d4"
    )
    assert templates["Runtime"].to_json()["Resources"]["Endpoint"]["DependsOn"] == [
        "DefaultLogs",
        "EndpointLogs",
    ]


def test_encrypted_logs_and_bounded_builds(templates: dict[str, Template]) -> None:
    for template in templates.values():
        for log in template.find_resources("AWS::Logs::LogGroup").values():
            assert log["Properties"]["KmsKeyId"]
            assert log["Properties"]["RetentionInDays"] <= 7
    templates["Build"].has_resource_properties(
        "AWS::CodeBuild::Project",
        {
            "TimeoutInMinutes": 25,
            "ConcurrentBuildLimit": 1,
            "Environment": Match.object_like({"Type": "ARM_CONTAINER", "PrivilegedMode": True}),
        },
    )


def test_original_name_expressions_are_preserved(templates: dict[str, Template]) -> None:
    resources = templates["Application"].to_json()["Resources"]
    assert resources["ApiFunction"]["Properties"]["FunctionName"] == {
        "Fn::Sub": "${AWS::StackName}-api",
    }
    assert resources["ApiLog"]["Properties"]["LogGroupName"] == {
        "Fn::Sub": "/aws/lambda/${AWS::StackName}-api",
    }
    assert resources["LoginDomain"]["Properties"]["Domain"] == {
        "Fn::Sub": "${AWS::StackName}-${AWS::AccountId}",
    }
    assert resources["ApiPermission"]["Properties"]["SourceAccount"] == {"Ref": "AWS::AccountId"}
    runtime = templates["Runtime"].to_json()["Resources"]
    assert runtime["EndpointLogs"]["Properties"]["LogGroupName"] == {
        "Fn::Sub": "/aws/bedrock-agentcore/runtimes/${Runtime.AgentRuntimeId}-probe",
    }
