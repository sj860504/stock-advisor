import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 모든 SQLAlchemy 모델 import → autogenerate 지원
from models.stock_meta import Base  # noqa: F401, E402
from models.portfolio import Portfolio, PortfolioHolding  # noqa: F401, E402
from models.trade_history import TradeHistory  # noqa: F401, E402
from models.settings import Settings  # noqa: F401, E402
from models.strategy_state import StrategyState  # noqa: F401, E402

target_metadata = Base.metadata


def get_url() -> str:
    """DB URL — repositories/database.py 의 DB_PATH 재사용."""
    from repositories.database import DB_PATH
    return f"sqlite:///{DB_PATH}"


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,   # SQLite ALTER TABLE 지원
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,   # SQLite ALTER TABLE 지원
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
