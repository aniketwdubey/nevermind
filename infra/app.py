import os
import re
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from nevermind_infra.app_stack import ApplicationStack
from nevermind_infra.compliance import existing_demo_exceptions
from nevermind_infra.runtime_stack import ENDPOINT, RuntimeStack

ROOT = Path(__file__).resolve().parents[1]
LAMBDA_CODE = ROOT / ".local/lambda-package"
WEBSITE_FILES = ROOT / "frontend/dist"


def main() -> None:
    project = os.environ.get("NEVERMIND_PROJECT", "nevermind")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,29}", project):
        raise ValueError("NEVERMIND_PROJECT must be 3-30 lowercase letters, digits or hyphens.")
    for path, command in [(LAMBDA_CODE, "deploy/package.sh"), (WEBSITE_FILES, "npm run build")]:
        if not path.is_dir():
            raise FileNotFoundError(f"{path} is missing. Run `make cdk-package` ({command}).")
    app = cdk.App()
    environment = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    )
    runtime = RuntimeStack(
        app,
        "Runtime",
        stack_name=f"{project}-model",
        env=environment,
        runtime_name=project.replace("-", "_") + "_decider",
        image_directory=ROOT / "deploy",
    )
    application = ApplicationStack(
        app,
        "Application",
        stack_name=f"{project}-app",
        env=environment,
        runtime_arn=runtime.runtime_arn,
        runtime_qualifier=ENDPOINT,
        lambda_code=LAMBDA_CODE,
        website_files=WEBSITE_FILES,
    )
    existing_demo_exceptions(runtime, application, runtime.runtime_name)
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    app.synth()


if __name__ == "__main__":
    main()
