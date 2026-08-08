"""Conexión compartida a PostgreSQL para el pipeline Bsale."""

from collections.abc import Iterator
from contextlib import contextmanager
import os

import psycopg
from psycopg import Connection


def get_database_url() -> str:
    """Obtiene la cadena de conexión desde el entorno."""
    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError(
            "Falta la variable de entorno DATABASE_URL. "
            "Configúrala localmente o como secreto de GitHub Actions."
        )

    return database_url


@contextmanager
def get_db_connection() -> Iterator[Connection]:
    """Abre una conexión transaccional y la cierra al finalizar."""
    try:
        with psycopg.connect(get_database_url()) as connection:
            yield connection
    except psycopg.Error as error:
        raise RuntimeError(
            f"No fue posible conectar con PostgreSQL: {error}"
        ) from error