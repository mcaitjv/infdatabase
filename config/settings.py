from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = ""

    # E-posta bildirimi (opsiyonel)
    alert_email_from: str = ""
    alert_email_to: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_password: str = ""

    @property
    def use_sqlite(self) -> bool:
        return not self.database_url


settings = Settings()
