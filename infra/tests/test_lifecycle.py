import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from lifecycle import Deployment, check_changes, confirm_destroy, validate_project


@pytest.mark.parametrize("project", ["ab", "bad/name", "../other", "OtherProject", "x" * 31])
def test_project_rejects_unsafe_names(project: str) -> None:
    with pytest.raises(ValueError):
        validate_project(project)


def test_project_accepts_a_separate_demo_name() -> None:
    assert validate_project("nevermind-test") == "nevermind-test"


@pytest.mark.parametrize(
    "action,replacement", [("Remove", "False"), ("Modify", "True"), ("Modify", "Conditional")]
)
def test_deployment_refuses_removal_and_possible_replacement(action: str, replacement: str) -> None:
    with pytest.raises(RuntimeError, match="Refusing"):
        check_changes(
            [
                {
                    "ResourceChange": {
                        "Action": action,
                        "LogicalResourceId": "State",
                        "Replacement": replacement,
                    },
                }
            ]
        )


def test_deployment_accepts_only_additions_and_in_place_changes() -> None:
    check_changes(
        [
            {"ResourceChange": {"Action": "Add", "LogicalResourceId": "Cleanup"}},
            {
                "ResourceChange": {
                    "Action": "Modify",
                    "LogicalResourceId": "State",
                    "Replacement": "False",
                }
            },
        ]
    )


@pytest.mark.parametrize(
    "response", ["", "yes", "DELETE wrong/nevermind", "DELETE 123456789012/other"]
)
def test_destroy_requires_exact_account_and_project(response: str) -> None:
    with patch("builtins.input", return_value=response):
        assert not confirm_destroy("123456789012", "nevermind")


def test_destroy_cancellation_never_calls_cdk() -> None:
    deployment = object.__new__(Deployment)
    deployment.account = "123456789012"
    deployment.project = "nevermind"
    with patch.object(Deployment, "cdk") as cdk, patch("builtins.input", return_value="no"):
        with pytest.raises(RuntimeError, match="not confirmed"):
            deployment.destroy()
        cdk.assert_not_called()


def test_confirmed_destroy_targets_only_the_three_project_stacks() -> None:
    deployment = object.__new__(Deployment)
    deployment.account = "123456789012"
    deployment.project = "nevermind"
    deployment.stacks = {"Application": "nevermind-app"}
    deployment.cf = Mock()
    deployment.logs = Mock()
    deployment.cf.get_paginator.return_value.paginate.return_value = [
        {
            "StackSummaries": [{"StackName": "nevermind-app", "StackStatus": "UPDATE_COMPLETE"}],
        }
    ]
    with (
        patch.object(Deployment, "cdk") as cdk,
        patch.object(Deployment, "cleanup_log_groups", return_value=["/aws/lambda/scoped-cleanup"]),
        patch(
            "builtins.input",
            return_value="DELETE 123456789012/nevermind",
        ),
    ):
        deployment.destroy()
        cdk.assert_called_once_with("destroy", "Application", "Runtime", "Build", "--force")
        deployment.logs.delete_log_group.assert_called_once_with(
            logGroupName="/aws/lambda/scoped-cleanup"
        )


def test_prepare_change_set_is_reviewed_before_execution() -> None:
    deployment = object.__new__(Deployment)
    deployment.stacks = {"Application": "nevermind-app"}
    deployment.cf = Mock()
    deployment.cf.get_paginator.return_value.paginate.return_value = [
        {
            "Summaries": [
                {
                    "ChangeSetName": "cdk-fixed",
                    "ChangeSetId": "change-id",
                    "Status": "CREATE_COMPLETE",
                }
            ],
        }
    ]
    deployment.cf.describe_change_set.return_value = {
        "Changes": [{"ResourceChange": {"Action": "Remove", "LogicalResourceId": "State"}}],
    }
    with (
        patch("lifecycle.uuid4") as uuid,
        patch.object(Deployment, "cdk") as cdk,
        patch("pathlib.Path.write_text"),
    ):
        uuid.return_value.hex = "fixed"
        deployment.directory = Path("unused")
        with pytest.raises(RuntimeError, match="Refusing"):
            deployment.deploy_stack("Application")
        assert cdk.call_count == 1
        assert "prepare-change-set" in cdk.call_args.args
        deployment.cf.execute_change_set.assert_not_called()
        deployment.cf.describe_change_set.assert_called_once_with(
            ChangeSetName="change-id",
            IncludePropertyValues=True,
        )


def test_unchanged_stack_is_explicit_and_never_executed() -> None:
    deployment = object.__new__(Deployment)
    deployment.stacks = {"Build": "nevermind-model-build"}
    deployment.cf = Mock()
    deployment.cf.get_paginator.return_value.paginate.return_value = [
        {
            "Summaries": [
                {
                    "ChangeSetName": "cdk-fixed",
                    "Status": "FAILED",
                    "StatusReason": "The submitted information didn't contain changes. Submit different information to create a change set.",
                }
            ],
        }
    ]
    with (
        patch("lifecycle.uuid4") as uuid,
        patch.object(Deployment, "cdk"),
        patch.object(Deployment, "bound_cleanup_logs") as logs,
    ):
        uuid.return_value.hex = "fixed"
        deployment.deploy_stack("Build")
        deployment.cf.execute_change_set.assert_not_called()
        logs.assert_called_once_with("Build")


def test_cleanup_log_discovery_uses_only_owned_provider_resources() -> None:
    deployment = object.__new__(Deployment)
    deployment.stacks = {"Build": "nevermind-model-build"}
    deployment.cf = Mock()
    deployment.cf.get_paginator.return_value.paginate.return_value = [
        {
            "StackResourceSummaries": [
                {
                    "ResourceType": "AWS::Lambda::Function",
                    "LogicalResourceId": "CustomS3AutoDeleteObjectsCustomResourceProviderHandler123",
                    "PhysicalResourceId": "owned-provider",
                },
                {
                    "ResourceType": "AWS::Lambda::Function",
                    "LogicalResourceId": "ApiFunction",
                    "PhysicalResourceId": "api",
                },
            ],
        }
    ]
    assert deployment.cleanup_log_groups("Build") == ["/aws/lambda/owned-provider"]
    deployment.cf.get_paginator.return_value.paginate.assert_called_once_with(
        StackName="nevermind-model-build"
    )


def test_evaluated_safe_change_is_executed_and_waited_for(tmp_path: Path) -> None:
    deployment = object.__new__(Deployment)
    deployment.stacks = {"Application": "nevermind-app"}
    deployment.directory = tmp_path
    deployment.cf = Mock()
    deployment.cf.get_paginator.return_value.paginate.return_value = [
        {
            "Summaries": [
                {
                    "ChangeSetName": "cdk-fixed",
                    "ChangeSetId": "change-id",
                    "Status": "CREATE_COMPLETE",
                }
            ],
        }
    ]
    deployment.cf.describe_change_set.return_value = {
        "Changes": [
            {
                "ResourceChange": {
                    "Action": "Modify",
                    "LogicalResourceId": "ApiStage",
                    "Replacement": "False",
                }
            }
        ],
    }
    deployment.cf.describe_events.return_value = {}
    deployment.cf.describe_stacks.return_value = {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
    with (
        patch("lifecycle.uuid4") as uuid,
        patch.object(Deployment, "cdk") as cdk,
        patch.object(Deployment, "bound_cleanup_logs") as logs,
    ):
        uuid.return_value.hex = "fixed"
        deployment.deploy_stack("Application")
        deployment.cf.execute_change_set.assert_called_once_with(ChangeSetName="change-id")
        deployment.cf.describe_change_set.assert_called_once_with(
            ChangeSetName="change-id",
            IncludePropertyValues=True,
        )
        deployment.cf.get_waiter.assert_called_once_with("stack_update_complete")
        deployment.cf.get_waiter.return_value.wait.assert_called_once_with(
            StackName="nevermind-app",
            WaiterConfig={"Delay": 10, "MaxAttempts": 360},
        )
        assert cdk.call_count == 1
        logs.assert_called_once_with("Application")


def test_infrastructure_and_backend_lock_shared_versions_match() -> None:
    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"^([a-zA-Z0-9_-]+)(?:\[[^]]+\])?==([^\s]+)")

    def pins(path: Path) -> dict[str, str]:
        return {
            match.group(1).lower(): match.group(2)
            for line in path.read_text().splitlines()
            if (match := pattern.match(line))
        }

    backend = pins(root / "requirements-dev.lock")
    infrastructure = pins(root / "infra/requirements.lock")
    for package in backend.keys() & infrastructure.keys():
        assert backend[package] == infrastructure[package], package
