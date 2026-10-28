"""Run the reusable assumption check without the website or workshop database."""

import argparse
import json

import boto3
from botocore.config import Config
from nevermind.clients import RELATION_QUESTION, Decider
from nevermind.policy import MIN_CONFIDENCE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check whether a changed goal invalidates an assumption."
    )
    parser.add_argument("--runtime-arn", help="Your deployed Decider AgentCore runtime ARN.")
    parser.add_argument("--qualifier", default="probe")
    parser.add_argument(
        "--preview", action="store_true", help="Print the request only; no inference or AWS access."
    )
    arguments = parser.parse_args()
    state = {
        "updated_requirement": "The workshop is online only. No attendees will be at the venue.",
        "assumption": "A physical room is needed for workshop attendees.",
    }
    if arguments.preview:
        print(json.dumps({"state": state, "questions": {"decision": RELATION_QUESTION}}, indent=2))
        return
    if not arguments.runtime_arn:
        parser.error("--runtime-arn is required unless --preview is selected")
    client = boto3.Session().client(
        "bedrock-agentcore",
        config=Config(
            connect_timeout=5,
            read_timeout=110,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )
    decider = Decider(client, arguments.runtime_arn, arguments.qualifier)
    try:
        decision = decider.decide("room", state)
        result = (
            "ask"
            if decision.confidence < MIN_CONFIDENCE or decision.choice == "unresolved"
            else decision.choice
        )
        print(
            json.dumps(
                {
                    "decision": decision.model_dump(),
                    "next_step": result,
                    "note": "This result is not approval to execute an action.",
                },
                indent=2,
            )
        )
    finally:
        decider.close()


if __name__ == "__main__":
    main()
