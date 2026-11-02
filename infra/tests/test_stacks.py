import json
import re
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Match, Template
from cdk_nag import AwsSolutionsChecks
from nevermind_infra.app_stack import ApplicationStack
from nevermind_infra.compliance import existing_demo_exceptions
from nevermind_infra.runtime_stack import ENDPOINT, RuntimeStack

ROOT = Path(__file__).resolve().parents[2]
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
    "Runtime": {"DefaultLogs", "Endpoint", "EndpointLogs", "Runtime", "RuntimeRole"},
}


@pytest.fixture(scope="module")
def templates(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Template]:
    code = tmp_path_factory.mktemp("lambda")
    (code / "handler.py").write_text("def handler(event, context): pass\n")
    site = tmp_path_factory.mktemp("site")
    (site / "assets").mkdir()
    (site / "assets/app.js").write_text("console.log('ok')\n")
    (site / "index.html").write_text("<!doctype html>\n")
    app = cdk.App(context={"@aws-cdk/core:defaultCrossStackReferences": "strong"})
    environment = cdk.Environment(account="123456789012", region="us-east-1")
    runtime = RuntimeStack(
        app,
        "Runtime",
        stack_name="example-model",
        env=environment,
        runtime_name="example_decider",
        image_directory=ROOT / "deploy",
    )
    application = ApplicationStack(
        app,
        "Application",
        stack_name="example-app",
        env=environment,
        runtime_arn=runtime.runtime_arn,
        runtime_qualifier=ENDPOINT,
        lambda_code=code,
        website_files=site,
    )
    existing_demo_exceptions(runtime, application, runtime.runtime_name)
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    result = {
        "Runtime": Template.from_stack(runtime),
        "Application": Template.from_stack(application),
    }
    for stack in (runtime, application):
        Annotations.from_stack(stack).has_no_error("*", Match.any_value())
        Annotations.from_stack(stack).has_no_warning("*", Match.any_value())
    return result


def test_original_logical_ids_remain_and_are_disposable(templates: dict[str, Template]) -> None:
    for name, original in ORIGINAL_IDS.items():
        resources = templates[name].to_json()["Resources"]
        assert original <= resources.keys()
        for resource in original:
            assert resources[resource]["DeletionPolicy"] == "Delete"


def test_no_customer_managed_keys_or_deployment_parameters(templates: dict[str, Template]) -> None:
    for template in templates.values():
        template.resource_count_is("AWS::KMS::Key", 0)
        for log in template.find_resources("AWS::Logs::LogGroup").values():
            assert "KmsKeyId" not in log["Properties"]
            assert log["Properties"]["RetentionInDays"] <= 7
        assert set(template.to_json().get("Parameters", {})) <= {"BootstrapVersion"}


def test_runtime_image_is_built_on_arm64_codebuild_before_runtime(
    templates: dict[str, Template],
) -> None:
    runtime = templates["Runtime"]
    runtime.resource_count_is("Custom::CDKContainerImageBuild", 1)
    runtime.has_resource_properties(
        "AWS::CodeBuild::Project",
        {"Environment": Match.object_like({"Type": "ARM_CONTAINER", "PrivilegedMode": True})},
    )
    build = runtime.find_resources("Custom::CDKContainerImageBuild")
    ((build_id, build_resource),) = build.items()
    assert "--platform linux/arm64" in build_resource["Properties"]["buildCommand"]
    assert "--file Dockerfile.decider-test" in build_resource["Properties"]["buildCommand"]
    resource = runtime.to_json()["Resources"]["Runtime"]
    assert build_id in resource["DependsOn"]
    uri = json.dumps(resource["Properties"]["AgentRuntimeArtifact"])
    assert build_id in uri


def test_runtime_limits_and_iam_authentication_are_preserved(
    templates: dict[str, Template],
) -> None:
    runtime = templates["Runtime"].to_json()["Resources"]["Runtime"]["Properties"]
    assert "AuthorizerConfiguration" not in runtime  # AgentCore's default is IAM, not anonymous.
    assert runtime["AgentRuntimeName"] == "example_decider"
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
    resources = templates["Runtime"].to_json()["Resources"]
    assert resources["Endpoint"]["DependsOn"] == ["DefaultLogs", "EndpointLogs"]
    assert resources["Endpoint"]["Properties"]["Name"] == "probe"


def test_lambda_code_and_runtime_come_from_cdk(templates: dict[str, Template]) -> None:
    resources = templates["Application"].to_json()["Resources"]
    for name in ["ApiFunction", "WorkerFunction"]:
        code = resources[name]["Properties"]["Code"]
        assert "cdk-hnb659fds-assets" in json.dumps(code["S3Bucket"])
        assert re.fullmatch(r"[0-9a-f]{64}\.zip", code["S3Key"])
        variables = resources[name]["Properties"]["Environment"]["Variables"]
        assert "Fn::ImportValue" in variables["NEVERMIND_RUNTIME_ARN"]
        assert variables["NEVERMIND_RUNTIME_QUALIFIER"] == "probe"


def test_website_and_sign_in_settings_are_uploaded_by_cdk(templates: dict[str, Template]) -> None:
    application = templates["Application"]
    deployments = application.find_resources("Custom::CDKBucketDeployment")
    assert len(deployments) == 2
    page = next(d for d in deployments.values() if d["Properties"].get("DistributionPaths"))
    assert page["Properties"]["DistributionPaths"] == ["/index.html", "/config.json"]
    assert page["Properties"]["Prune"] is False
    markers = json.dumps(page["Properties"]["SourceMarkers"])
    assert "BrowserClient" in markers and "LoginDomain" in markers and "Website" in markers
    assets = next(d for d in deployments.values() if not d["Properties"].get("DistributionPaths"))
    assert assets["Properties"]["DestinationBucketKeyPrefix"] == "assets/"


def test_disposable_website_bucket(templates: dict[str, Template]) -> None:
    application = templates["Application"]
    application.resource_count_is("Custom::S3AutoDeleteObjects", 1)
    application.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "VersioningConfiguration": {"Status": "Enabled"},
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_application_auth_cache_and_state_are_unchanged(templates: dict[str, Template]) -> None:
    application = templates["Application"]
    application.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {"AuthorizationType": "JWT", "AuthorizationScopes": ["openid"]},
    )
    application.has_resource_properties(
        "AWS::Cognito::UserPool",
        {"AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}, "UserPoolTier": "ESSENTIALS"},
    )
    application.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {"GenerateSecret": False, "AllowedOAuthFlows": ["code"]},
    )
    application.has_resource_properties(
        "AWS::CloudFront::CachePolicy",
        {"CachePolicyConfig": Match.object_like({"MinTTL": 0, "DefaultTTL": 0, "MaxTTL": 0})},
    )
    application.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}},
    )


def test_original_name_expressions_are_preserved(templates: dict[str, Template]) -> None:
    resources = templates["Application"].to_json()["Resources"]
    assert resources["ApiFunction"]["Properties"]["FunctionName"] == {
        "Fn::Sub": "${AWS::StackName}-api",
    }
    assert resources["LoginDomain"]["Properties"]["Domain"] == {
        "Fn::Sub": "${AWS::StackName}-${AWS::AccountId}",
    }
    runtime = templates["Runtime"].to_json()["Resources"]
    assert runtime["EndpointLogs"]["Properties"]["LogGroupName"] == {
        "Fn::Sub": "/aws/bedrock-agentcore/runtimes/${Runtime.AgentRuntimeId}-probe",
    }


def test_infrastructure_and_backend_lock_shared_versions_match() -> None:
    pattern = re.compile(r"^([a-zA-Z0-9_-]+)(?:\[[^]]+\])?==([^\s]+)")

    def pins(path: Path) -> dict[str, str]:
        return {
            match.group(1).lower(): match.group(2)
            for line in path.read_text().splitlines()
            if (match := pattern.match(line))
        }

    backend = pins(ROOT / "requirements-dev.lock")
    infrastructure = pins(ROOT / "infra/requirements.lock")
    for package in backend.keys() & infrastructure.keys():
        assert backend[package] == infrastructure[package], package
