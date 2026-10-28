import boto3
import pytest
from moto import mock_aws
from nevermind.store import Store


@pytest.fixture
def store():
    with mock_aws():
        session = boto3.Session(region_name="us-east-1")
        client = session.client("dynamodb")
        client.create_table(
            TableName="workshops",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
        )
        yield Store(client, "workshops")
