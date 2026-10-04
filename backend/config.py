from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://shroommap:shroommap@db:5432/shroommap"
    # "passphrase:Display Name,passphrase2:Other Name"
    add_leaf_passphrases: str = "changeme:Someone"
    uploads_dir: str = "/data/uploads"
    max_photo_mb: int = 15

    # Comma-separated Google AI Studio API keys (aistudio.google.com/apikey),
    # each one a separate free account/quota to rotate across.
    gemini_api_keys: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    # Conservative default for a free-tier key's shared daily request budget;
    # check ai.google.dev/gemini-api/docs/rate-limits for current numbers and
    # adjust per your actual plan. This app counts only ITS OWN calls - if the
    # same keys are used by another app, split the budget between them here.
    gemini_daily_limit_per_key: int = 40

    @property
    def passphrase_names(self) -> dict[str, str]:
        pairs = (p.strip() for p in self.add_leaf_passphrases.split(","))
        return dict(pair.split(":", 1) for pair in pairs if ":" in pair)

    @property
    def gemini_keys(self) -> list[str]:
        return [k.strip() for k in self.gemini_api_keys.split(",") if k.strip()]


settings = Settings()
