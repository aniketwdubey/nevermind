import logging
from typing import Any

from mangum import Mangum

from .api import app, service
from .models import Job

logging.getLogger().setLevel(logging.INFO)
adapter = Mangum(app, lifespan="off")


def handler(event: dict[str, Any], context: Any) -> Any:
    if "Records" in event:
        failures = []
        for record in event["Records"]:
            try:
                service().process(Job.model_validate_json(record["body"]))
            except Exception as error:
                logging.error("Background job delivery failed type=%s", type(error).__name__)
                failures.append({"itemIdentifier": record["messageId"]})
        return {"batchItemFailures": failures}
    return adapter(event, context)
