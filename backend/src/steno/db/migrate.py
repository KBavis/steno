from importlib.resources import files

from alembic import command
from alembic.config import Config


def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(files("steno.db") / "migrations"))
    return cfg


def upgrade(revision: str = "head") -> None:
    command.upgrade(_config(), revision)
