from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    MEALIE_HOST: str
    MEALIE_TOKEN: str
    PICNIC_USERNAME: str = ""
    PICNIC_PASSWORD: str = ""
    PICNIC_AUTH_TOKEN: str = ""
    PICNIC_COUNTRY_CODE: str = "NL"
    FUZZY_THRESHOLD: int = 65

    # LLM Matching (optional, replaces fuzzy matching when enabled)
    ANTHROPIC_API_KEY: str = ""
    LLM_MATCHING_ENABLED: bool = False
    LLM_MODEL: str = "claude-haiku-4-5-20251001"
    LLM_MAX_PRODUCTS_PER_ITEM: int = 15


settings = Settings()
