from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEVERMIND_")
    table_name: str
    queue_url: str
    runtime_arn: str
    runtime_qualifier: str = "probe"
    model_id: str = "us.amazon.nova-2-lite-v1:0"
