import os
import re

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from nevermind_infra.app_stack import ApplicationStack
from nevermind_infra.build_stack import BuildStack
from nevermind_infra.compliance import existing_demo_exceptions
from nevermind_infra.runtime_stack import RuntimeStack


def main() -> None:
    project = os.environ.get("NEVERMIND_PROJECT", "nevermind")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,29}", project):
        raise ValueError("NEVERMIND_PROJECT must be 3-30 lowercase letters, digits or hyphens.")
    app = cdk.App()
    environment = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    )
    build = BuildStack(app, "Build", stack_name=f"{project}-model-build", env=environment)
    runtime = RuntimeStack(app, "Runtime", stack_name=f"{project}-model", env=environment)
    application = ApplicationStack(app, "Application", stack_name=f"{project}-app", env=environment)
    runtime.add_stack_dependency(build)
    application.add_stack_dependency(runtime)
    existing_demo_exceptions(build, runtime, application)
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    app.synth()


if __name__ == "__main__":
    main()
