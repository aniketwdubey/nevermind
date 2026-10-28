import argparse
import getpass
import json
import os
import re
import secrets
from pathlib import Path

import boto3


def main() -> None:
    parser = argparse.ArgumentParser(description="Create one invited sandbox user without logging a password.")
    parser.add_argument("username")
    parser.add_argument("--generate", action="store_true", help="Save a generated password to an ignored, owner-readable file.")
    arguments = parser.parse_args()
    if not os.environ.get("AWS_PROFILE"):
        parser.error("Set AWS_PROFILE to the approved project profile.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,50}", arguments.username):
        parser.error("Use 3-50 letters, numbers, underscores, or hyphens.")
    directory = Path(".local/deploy")
    output = directory / f"login-{arguments.username}.json"
    if output.exists():
        parser.error("A local login file already exists. Do not overwrite an existing user's credentials.")
    outputs = {
        entry["OutputKey"]: entry["OutputValue"]
        for entry in json.loads((directory / "app-outputs.json").read_text())
    }
    password = "Aa9!" + secrets.token_urlsafe(24) if arguments.generate else getpass.getpass("New user password: ")
    if not arguments.generate and password != getpass.getpass("Confirm password: "):
        parser.error("The passwords do not match.")
    client = boto3.Session().client("cognito-idp")
    client.admin_create_user(
        UserPoolId=outputs["UserPoolId"], Username=arguments.username,
        TemporaryPassword=password, MessageAction="SUPPRESS",
    )
    client.admin_set_user_password(
        UserPoolId=outputs["UserPoolId"], Username=arguments.username,
        Password=password, Permanent=True,
    )
    if arguments.generate:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump({"username": arguments.username, "password": password,
                       "website": outputs["WebsiteUrl"], "user_pool": outputs["UserPoolId"],
                       "client_id": outputs["ClientId"]}, file, indent=2)
        print(f"Invited user created. Credentials saved locally to {output}; do not publish this file.")
    else:
        print("Invited user created. No password was saved.")


if __name__ == "__main__":
    main()
