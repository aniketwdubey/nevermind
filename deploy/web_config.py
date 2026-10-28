import json
from pathlib import Path


def main() -> None:
    directory = Path(".local/deploy")
    outputs = {
        entry["OutputKey"]: entry["OutputValue"]
        for entry in json.loads((directory / "app-outputs.json").read_text())
    }
    (directory / "config.json").write_text(
        json.dumps(
            {
                "clientId": outputs["ClientId"],
                "loginUrl": outputs["LoginUrl"],
                "redirectUrl": outputs["WebsiteUrl"] + "/",
            }
        )
    )


if __name__ == "__main__":
    main()
