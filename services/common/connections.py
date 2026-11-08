"""Підключення до PostgreSQL і RabbitMQ з повторними спробами.

Контейнери стартують не одночасно, тому перші кілька спроб можуть
завершитись помилкою, це нормально.
"""

import logging
import os
import time

import pika
import psycopg

log = logging.getLogger(__name__)


def _retry(what: str, connect, attempts: int = 30, delay: float = 2.0):
    for attempt in range(1, attempts + 1):
        try:
            return connect()
        except Exception as exc:  # noqa: BLE001
            log.warning(f"{what} недоступний, спроба {attempt}/{attempts}: {exc}")
            time.sleep(delay)
    raise RuntimeError(f"не вдалося підключитися до {what}")


def connect_postgres() -> psycopg.Connection:
    dsn = os.environ["POSTGRES_DSN"]
    return _retry("PostgreSQL", lambda: psycopg.connect(dsn, autocommit=True))


def connect_rabbit(queue: str) -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    params = pika.URLParameters(os.environ["RABBITMQ_URL"])
    params.heartbeat = 30
    connection = _retry("RabbitMQ", lambda: pika.BlockingConnection(params))
    channel = connection.channel()
    channel.queue_declare(queue=queue, durable=True)
    return connection, channel
