from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine

import settings.settings as settings


_DEFAULT_CONNECT_ARGS = {"check_same_thread": False}


def create_sqlite_engine(
    *,
    db_path: str | None = None,
    connect_args: dict | None = None,
):
    merged_connect_args = dict(_DEFAULT_CONNECT_ARGS)
    if connect_args:
        merged_connect_args.update(connect_args)
    return create_engine(
        f"sqlite:///{db_path or settings.db_path}",
        connect_args=merged_connect_args,
    )


@lru_cache(maxsize=1)
def get_engine():
    return create_sqlite_engine()


def get_engine_with_timeout(timeout: int = 15):
    return create_sqlite_engine(connect_args={"timeout": timeout})
