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


settings = Settings()
