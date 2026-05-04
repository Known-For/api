import os
from functools import lru_cache
from pathlib import Path


class Settings:
    """Process-wide configuration loaded from environment."""

    api_key: str
    notion_token: str
    notion_api_version: str
    data_dir: Path
    clients_path: Path

    def __init__(self) -> None:
        self.api_key = os.environ.get("KF_API_KEY", "")
        self.notion_token = os.environ.get("NOTION_API_TOKEN", "")
        self.notion_api_version = os.environ.get("NOTION_API_VERSION", "2022-06-28")
        self.data_dir = Path(os.environ.get("KF_DATA_DIR", "./data"))
        repo_root = Path(__file__).resolve().parent.parent
        self.clients_path = Path(
            os.environ.get("KF_CLIENTS_PATH", str(repo_root / "clients.json"))
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
