from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from mypy_boto3_cloudformation.type_defs import ChangeSetSummaryTypeDef

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"


def validate_project(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,29}", value):
        raise ValueError("NEVERMIND_PROJECT must be 3-30 lowercase letters, digits or hyphens.")
    return value


def check_changes(changes: Sequence[Mapping[str, object]]) -> None:
    for change in changes:
        resource = change["ResourceChange"]
        if not isinstance(resource, dict):
            raise TypeError("CloudFormation returned an invalid resource change.")
        print(resource["Action"], resource["LogicalResourceId"], resource.get("Replacement", ""))
        if resource["Action"] not in {"Add", "Modify"} or (
            resource["Action"] == "Modify" and resource.get("Replacement") != "False"
        ):
            raise RuntimeError(
                "Refusing resource removal or replacement. Review this change set manually."
            )


def confirm_destroy(account: str, project: str) -> bool:
    expected = f"DELETE {account}/{project}"
    print(
        f"This deletes the {project} demo's workshop data, users, files, images and logs in account {account}.\n"
        "It leaves the shared CDK bootstrap and any old unmanaged resources alone.\n"
        "Encryption keys enter a seven-day deletion waiting period.\n"
        f"To continue, type exactly: {expected}",
        flush=True,
    )
    return input("> ") == expected


class Deployment:
    def __init__(self, profile: str, project: str, region: str) -> None:
        self.project = validate_project(project)
        if region != "us-east-1":
            raise ValueError("This tested demonstration currently supports us-east-1 only.")
        self.region = region
        self.environment = dict(os.environ) | {
            "AWS_PROFILE": profile,
            "AWS_REGION": region,
            "AWS_DEFAULT_REGION": region,
            "NEVERMIND_PROJECT": project,
            "AWS_PAGER": "",
            "PATH": str(INFRA / "node_modules/.bin") + os.pathsep + os.environ["PATH"],
        }
        session = boto3.Session(profile_name=profile, region_name=region)
        config = Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"total_max_attempts": 2, "mode": "standard"},
        )
        self.cf = session.client("cloudformation", config=config)
        self.s3 = session.client("s3", config=config)
        self.ecr = session.client("ecr", config=config)
        self.build = session.client("codebuild", config=config)
        self.cloudfront = session.client("cloudfront", config=config)
        self.logs = session.client("logs", config=config)
        self.control = session.client("bedrock-agentcore-control", config=config)
        self.account = session.client("sts", config=config).get_caller_identity()["Account"]
        session.client("ssm", config=config).get_parameter(Name="/cdk-bootstrap/hnb659fds/version")
        self.directory = ROOT / ".local/cdk" / project
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stacks = {
            "Build": f"{project}-model-build",
            "Runtime": f"{project}-model",
            "Application": f"{project}-app",
        }
        print(f"Target account {self.account}, region {region}, project {project}", flush=True)

    def run(self, args: list[str], *, cwd: Path = ROOT, timeout: int = 1800) -> None:
        subprocess.run(args, cwd=cwd, env=self.environment, check=True, timeout=timeout)

    def cdk(self, *args: str) -> None:
        self.run(
            [
                str(INFRA / "node_modules/.bin/node"),
                str(INFRA / "node_modules/aws-cdk/bin/cdk"),
                *args,
            ],
            cwd=INFRA,
            timeout=3600,
        )

    def outputs(self, name: str) -> dict[str, str]:
        stack = self.cf.describe_stacks(StackName=self.stacks[name])["Stacks"][0]
        result = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
        (self.directory / f"{name}-outputs.json").write_text(json.dumps(result, indent=2))
        return result

    def cleanup_log_groups(self, name: str) -> list[str]:
        groups = []
        for page in self.cf.get_paginator("list_stack_resources").paginate(
            StackName=self.stacks[name]
        ):
            for resource in page["StackResourceSummaries"]:
                if resource["ResourceType"] == "AWS::Lambda::Function" and resource[
                    "LogicalResourceId"
                ].startswith("CustomS3AutoDeleteObjectsCustomResourceProviderHandler"):
                    groups.append("/aws/lambda/" + resource["PhysicalResourceId"])
        return groups

    def bound_cleanup_logs(self, name: str) -> None:
        for group in self.cleanup_log_groups(name):
            try:
                self.logs.put_retention_policy(logGroupName=group, retentionInDays=7)
            except self.logs.exceptions.ResourceNotFoundException:
                print("Cleanup provider has not created its log group yet:", group, flush=True)

    def deploy_stack(self, name: str, parameters: Mapping[str, str] | None = None) -> None:
        change_name = "cdk-" + uuid4().hex
        args = [
            "deploy",
            name,
            "--exclusively",
            "--strict",
            "--require-approval",
            "never",
            "--method",
            "prepare-change-set",
            "--change-set-name",
            change_name,
        ]
        if parameters:
            args.append("--parameters")
            args.extend(f"{name}:{key}={value}" for key, value in parameters.items())
        self.cdk(*args)
        found: ChangeSetSummaryTypeDef | None = None
        for page in self.cf.get_paginator("list_change_sets").paginate(StackName=self.stacks[name]):
            for item in page["Summaries"]:
                if item["ChangeSetName"] == change_name:
                    found = item
        if found is None:
            print(
                f"No change set created for {name}; CDK reported no infrastructure changes.",
                flush=True,
            )
            self.bound_cleanup_logs(name)
            return
        if found["Status"] == "FAILED" and found.get("StatusReason", "").startswith(
            "The submitted information didn't contain changes."
        ):
            print(f"No infrastructure changes for {name}.", flush=True)
            self.bound_cleanup_logs(name)
            return
        if found["Status"] != "CREATE_COMPLETE":
            raise RuntimeError(
                f"Change set is not ready: {found.get('StatusReason', found['Status'])}"
            )
        details = self.cf.describe_change_set(
            ChangeSetName=found["ChangeSetId"],
            IncludePropertyValues=True,
        )
        if "NextToken" in details:
            raise RuntimeError("Change set exceeds the supported review size; inspect it manually.")
        (self.directory / f"{name}-changes.json").write_text(
            json.dumps(details, indent=2, default=str)
        )
        check_changes(details["Changes"])
        events = self.cf.describe_events(ChangeSetName=found["ChangeSetId"])
        (self.directory / f"{name}-validation.json").write_text(
            json.dumps(events, indent=2, default=str)
        )
        current = self.cf.describe_stacks(StackName=self.stacks[name])["Stacks"][0]
        creating = current["StackStatus"] == "REVIEW_IN_PROGRESS"
        # Execute the reviewed CDK change set directly; the CLI's metadata-only diff renderer can fail.
        self.cf.execute_change_set(ChangeSetName=found["ChangeSetId"])
        waiter = (
            self.cf.get_waiter("stack_create_complete")
            if creating
            else self.cf.get_waiter("stack_update_complete")
        )
        waiter.wait(
            StackName=self.stacks[name],
            WaiterConfig={"Delay": 10, "MaxAttempts": 360},
        )
        print(f"{name} deployment complete.", flush=True)
        self.bound_cleanup_logs(name)

    def image(self) -> dict[str, str]:
        self.deploy_stack("Build")
        outputs = self.outputs("Build")
        archive = self.directory / "runtime-source.zip"
        self.run([sys.executable, "deploy/runtime_source.py", str(archive)])
        tag = "source-" + hashlib.sha256(archive.read_bytes()).hexdigest()
        try:
            image = self.ecr.describe_images(
                repositoryName=outputs["RepositoryName"],
                imageIds=[{"imageTag": tag}],
            )["imageDetails"][0]
        except self.ecr.exceptions.ImageNotFoundException:
            image = None
        if image is None:
            uploaded = self.s3.put_object(
                Bucket=outputs["SourceBucket"],
                Key="input/source.zip",
                Body=archive.read_bytes(),
                ServerSideEncryption="AES256",
            )
            uri = outputs["RepositoryUri"] + ":" + tag
            result = self.build.start_build(
                projectName=outputs["BuildProject"],
                sourceVersion=uploaded["VersionId"],
                environmentVariablesOverride=[
                    {"name": "REGISTRY", "value": uri.split("/")[0], "type": "PLAINTEXT"},
                    {"name": "IMAGE_URI", "value": uri, "type": "PLAINTEXT"},
                ],
            )["build"]
            build_id = result["id"]
            (self.directory / "build-id").write_text(build_id)
            for _ in range(125):
                result = self.build.batch_get_builds(ids=[build_id])["builds"][0]
                status = result["buildStatus"]
                print("Model image build:", status, flush=True)
                if status != "IN_PROGRESS":
                    break
                time.sleep(15)
            else:
                self.build.stop_build(id=build_id)
                raise TimeoutError("Model image build exceeded the bounded wait and was stopped.")
            if status != "SUCCEEDED":
                raise RuntimeError(f"Model image build failed: {status}; inspect build {build_id}.")
            image = self.ecr.describe_images(
                repositoryName=outputs["RepositoryName"],
                imageIds=[{"imageTag": tag}],
            )["imageDetails"][0]
        else:
            print("Reusing the existing immutable model image.", flush=True)
        return {
            "RuntimeName": self.project.replace("-", "_") + "_decider",
            "ImageUri": outputs["RepositoryUri"] + "@" + image["imageDigest"],
            "RepositoryArn": outputs["RepositoryArn"],
            "LogKeyArn": outputs["LogKeyArn"],
        }

    def runtime(self) -> dict[str, str]:
        self.deploy_stack("Runtime", self.image())
        outputs = self.outputs("Runtime")
        endpoint = self.control.get_agent_runtime_endpoint(
            agentRuntimeId=outputs["RuntimeId"],
            endpointName="probe",
        )
        current = self.control.get_agent_runtime(agentRuntimeId=outputs["RuntimeId"])
        if (
            endpoint["status"] != "READY"
            or endpoint["liveVersion"] != current["agentRuntimeVersion"]
        ):
            raise RuntimeError("Runtime endpoint is not ready on the deployed version.")
        return outputs

    def application(self) -> None:
        runtime = self.runtime()
        self.run(["bash", "deploy/package.sh"])
        archive = Path((ROOT / ".local/package-path").read_text().strip())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        source = self.outputs("Build")["SourceBucket"]
        key = f"lambda/{digest}.zip"
        self.s3.upload_file(str(archive), source, key, ExtraArgs={"ServerSideEncryption": "AES256"})
        self.deploy_stack(
            "Application",
            {
                "CodeBucket": source,
                "CodeKey": key,
                "RuntimeArn": runtime["RuntimeArn"],
                "RuntimeQualifier": "probe",
            },
        )
        self.run(["npm", "run", "build"], cwd=ROOT / "frontend", timeout=300)
        outputs = self.outputs("Application")
        deploy = ROOT / ".local/deploy"
        deploy.mkdir(exist_ok=True)
        public_outputs = [
            {"OutputKey": key, "OutputValue": value} for key, value in outputs.items()
        ]
        (deploy / "app-outputs.json").write_text(json.dumps(public_outputs, indent=2))
        self.run([sys.executable, "deploy/web_config.py"])
        for path in sorted((ROOT / "frontend/dist").rglob("*")):
            if not path.is_file():
                continue
            cache = "no-cache" if path.name == "index.html" else "public,max-age=31536000,immutable"
            self.s3.upload_file(
                str(path),
                outputs["WebBucket"],
                path.relative_to(ROOT / "frontend/dist").as_posix(),
                ExtraArgs={
                    "CacheControl": cache,
                    "ContentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    "ServerSideEncryption": "AES256",
                },
            )
        self.s3.upload_file(
            str(deploy / "config.json"),
            outputs["WebBucket"],
            "config.json",
            ExtraArgs={
                "CacheControl": "no-store",
                "ContentType": "application/json",
                "ServerSideEncryption": "AES256",
            },
        )
        self.cloudfront.create_invalidation(
            DistributionId=outputs["DistributionId"],
            InvalidationBatch={
                "Paths": {"Quantity": 2, "Items": ["/index.html", "/config.json"]},
                "CallerReference": uuid4().hex,
            },
        )
        print("Website:", outputs["WebsiteUrl"], flush=True)

    def destroy(self) -> None:
        if not confirm_destroy(self.account, self.project):
            raise RuntimeError("Destruction was not confirmed. No resources were deleted.")
        live = {
            stack["StackName"]
            for page in self.cf.get_paginator("list_stacks").paginate()
            for stack in page["StackSummaries"]
            if stack["StackStatus"] != "DELETE_COMPLETE"
        }
        groups = [
            group
            for name, stack in self.stacks.items()
            if stack in live
            for group in self.cleanup_log_groups(name)
        ]
        self.cdk("destroy", "Application", "Runtime", "Build", "--force")
        for group in groups:
            try:
                self.logs.delete_log_group(logGroupName=group)
            except self.logs.exceptions.ResourceNotFoundException:
                print("Cleanup provider log group already absent:", group, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy or explicitly destroy only Nevermind's CDK stacks."
    )
    parser.add_argument("command", choices=["deploy", "runtime", "destroy", "outputs"])
    args = parser.parse_args()
    profile = os.environ.get("AWS_PROFILE")
    if not profile:
        parser.error("Set AWS_PROFILE to the approved project account profile.")
    for old in ("NEVERMIND_STACK_NAME", "NEVERMIND_RUNTIME_STACK", "NEVERMIND_RUNTIME_NAME"):
        if old in os.environ:
            parser.error(
                f"{old} is replaced by NEVERMIND_PROJECT, which names all three stacks consistently."
            )
    deployment = Deployment(
        profile,
        os.environ.get("NEVERMIND_PROJECT", "nevermind"),
        os.environ.get("AWS_REGION", "us-east-1"),
    )
    if args.command == "deploy":
        deployment.application()
    elif args.command == "runtime":
        print(deployment.runtime()["RuntimeArn"])
    elif args.command == "destroy":
        deployment.destroy()
    else:
        print(json.dumps(deployment.outputs("Application"), indent=2))


if __name__ == "__main__":
    main()
