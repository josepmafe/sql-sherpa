from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EVALS_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = EVALS_ROOT / "cases.yaml"
PRICES_PATH = EVALS_ROOT / "prices.yaml"
RUNS_DIR = EVALS_ROOT / "runs"


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    eval_db_name: str = "sherpa_eval"
    agent_user: str
    agent_password: SecretStr
    admin_user: str
    admin_password: SecretStr
    eval_target_path: Path = Path("data-platform/dbt/target-eval")
    default_variant: str = "v0_baseline"
