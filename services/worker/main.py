"""Worker: читає події з RabbitMQ і записує їх у PostgreSQL.

Окремий потік раз на ANALYTICS_INTERVAL секунд рахує агрегати
(кількість замовлень за статусами, виручку по містах) і віддає їх
у Prometheus як gauge-метрики.
"""

import json
import os
import threading
import time

import psycopg
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from common.connections import connect_postgres, connect_rabbit
from common.telemetry import setup

log = setup(os.getenv("SERVICE_NAME", "worker"))
tracer = trace.get_tracer("worker")

QUEUE = os.getenv("ORDERS_QUEUE", "orders")
ANALYTICS_INTERVAL = int(os.getenv("ANALYTICS_INTERVAL", "15"))

events_processed = Counter(
    "fd_events_processed_total", "Оброблені події з черги", ["event", "result"]
)
db_query_seconds = Histogram(
    "fd_db_query_seconds", "Тривалість SQL-запитів", ["query"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)
delivery_seconds = Histogram(
    "fd_delivery_time_seconds", "Час від створення замовлення до доставки",
    buckets=(15, 20, 25, 30, 35, 40, 50, 60, 75, 90),
)
orders_last_hour = Gauge("fd_orders_last_hour", "Замовлення за останню годину", ["status"])
revenue_last_hour = Gauge("fd_revenue_last_hour_uah", "Виручка доставлених замовлень за годину, грн", ["city"])
avg_check = Gauge("fd_avg_check_uah", "Середній чек за останню годину, грн")

SQL_INSERT_ORDER = """
    INSERT INTO orders (id, customer_id, restaurant_id, amount, status, created_at)
    VALUES (%(order_id)s, %(customer_id)s, %(restaurant_id)s, %(amount)s, 'created', to_timestamp(%(ts)s))
"""
SQL_UPDATE_STATUS = """
    UPDATE orders
       SET status = %(status)s::order_status,
           courier_id = COALESCE(%(courier_id)s::int, courier_id),
           updated_at = to_timestamp(%(ts)s),
           delivered_at = CASE WHEN %(status)s::order_status = 'delivered'
                               THEN to_timestamp(%(ts)s) ELSE delivered_at END
     WHERE id = %(order_id)s
 RETURNING EXTRACT(EPOCH FROM updated_at - created_at)
"""
SQL_INSERT_EVENT = """
    INSERT INTO order_events (order_id, status, created_at)
    VALUES (%(order_id)s, %(status)s, to_timestamp(%(ts)s))
"""
SQL_STATUS_STATS = """
    SELECT status, count(*) FROM orders
     WHERE created_at > now() - interval '1 hour'
     GROUP BY status
"""
SQL_REVENUE_STATS = """
    SELECT r.city, sum(o.amount)
      FROM orders o JOIN restaurants r ON r.id = o.restaurant_id
     WHERE o.status = 'delivered' AND o.delivered_at > now() - interval '1 hour'
     GROUP BY r.city
"""


def execute(conn: psycopg.Connection, name: str, sql: str, params: dict | None = None):
    """Виконує запит, міряє час (Prometheus) і створює span (Tempo)."""
    with tracer.start_as_current_span(f"db {name}", kind=SpanKind.CLIENT) as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.statement", " ".join(sql.split()))
        with db_query_seconds.labels(name).time():
            return conn.execute(sql, params)


def handle_event(conn: psycopg.Connection, event: dict):
    if event["type"] == "order_created":
        with conn.transaction():
            execute(conn, "insert_order", SQL_INSERT_ORDER, event)
            execute(conn, "insert_event", SQL_INSERT_EVENT, event)
        return

    params = {"courier_id": None, **event}
    with conn.transaction():
        row = execute(conn, "update_status", SQL_UPDATE_STATUS, params).fetchone()
        if row is None:
            raise LookupError(f"замовлення {event['order_id']} не знайдено")
        execute(conn, "insert_event", SQL_INSERT_EVENT, params)

    if event["status"] == "delivered":
        delivery_seconds.observe(float(row[0]))


def consume():
    conn = connect_postgres()
    connection, channel = connect_rabbit(QUEUE)
    channel.basic_qos(prefetch_count=20)

    def on_message(ch, method, properties, body):
        ctx = propagate.extract(properties.headers or {})
        event = json.loads(body)
        status = event.get("status", "unknown")

        with tracer.start_as_current_span(f"process {event['type']}", context=ctx, kind=SpanKind.CONSUMER) as span:
            span.set_attribute("messaging.system", "rabbitmq")
            span.set_attribute("order.id", event["order_id"])
            span.set_attribute("order.status", status)
            try:
                handle_event(conn, event)
            except (psycopg.Error, LookupError) as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                events_processed.labels(status, "error").inc()
                log.error("не вдалося обробити подію", exc_info=True,
                          extra={"fields": {"order": event["order_id"], "status": status}})
                # бите повідомлення не повертаємо в чергу, інакше воно крутитиметься вічно
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return

            events_processed.labels(status, "ok").inc()
            log.info("подію збережено", extra={"fields": {"order": event["order_id"], "status": status}})
            ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=QUEUE, on_message_callback=on_message)
    log.info("очікую повідомлення", extra={"fields": {"queue": QUEUE}})
    channel.start_consuming()


def analytics_loop():
    conn = connect_postgres()
    while True:
        try:
            with tracer.start_as_current_span("analytics refresh"):
                rows = execute(conn, "status_stats", SQL_STATUS_STATS).fetchall()
                counts = dict(rows)
                for status in ("created", "cooking", "on_the_way", "delivered", "cancelled"):
                    orders_last_hour.labels(status).set(counts.get(status, 0))

                total = 0.0
                for city, revenue in execute(conn, "revenue_stats", SQL_REVENUE_STATS).fetchall():
                    revenue_last_hour.labels(city).set(float(revenue))
                    total += float(revenue)
                delivered = counts.get("delivered", 0)
                avg_check.set(total / delivered if delivered else 0)
        except psycopg.Error:
            log.exception("помилка під час підрахунку аналітики")
        time.sleep(ANALYTICS_INTERVAL)


if __name__ == "__main__":
    start_http_server(8001)
    threading.Thread(target=analytics_loop, daemon=True, name="analytics").start()
    consume()
