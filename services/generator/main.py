"""Генератор навантаження: імітує роботу сервісу доставки.

Створює нові замовлення і просуває їх по статусах
created -> cooking -> on_the_way -> delivered (або cancelled).
Кожна зміна - окреме повідомлення в черзі RabbitMQ, у базу пише worker.
"""

import json
import math
import os
import random
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass

import pika
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind
from prometheus_client import Counter, Gauge, start_http_server

from common.connections import connect_postgres, connect_rabbit
from common.telemetry import setup

log = setup(os.getenv("SERVICE_NAME", "generator"))
tracer = trace.get_tracer("generator")

QUEUE = os.getenv("ORDERS_QUEUE", "orders")
ORDERS_PER_MINUTE = float(os.getenv("ORDERS_PER_MINUTE", "40"))
BROKEN_ORDER_CHANCE = float(os.getenv("BROKEN_ORDER_CHANCE", "0.01"))
CANCEL_CHANCE = 0.08
TICK = 0.25  # секунди

# Час у симуляції прискорений: 1 секунда ~ 1 хвилина реального життя.
# статус -> (наступний статус, мін. тривалість, макс. тривалість)
STAGES = {
    "created": ("cooking", 2, 6),
    "cooking": ("on_the_way", 8, 20),
    "on_the_way": ("delivered", 10, 35),
}

events_published = Counter("fd_events_published_total", "Події, відправлені в чергу", ["event"])
orders_created = Counter("fd_orders_created_total", "Створені замовлення", ["city"])
orders_in_flight = Gauge("fd_generator_orders_in_flight", "Незавершені замовлення в генераторі")
current_rate = Gauge("fd_generator_target_rate", "Цільова кількість замовлень за хвилину")


@dataclass
class ActiveOrder:
    id: str
    city: str
    status: str
    due: float


class Generator:
    def __init__(self):
        self.restaurants: list[tuple[int, str]] = []
        self.customers: dict[str, list[int]] = defaultdict(list)
        self.couriers: dict[str, list[int]] = defaultdict(list)
        self.active: list[ActiveOrder] = []
        self.started = time.monotonic()
        self.connection, self.channel = connect_rabbit(QUEUE)

    def load_reference_data(self):
        """Ресторани, клієнти й кур'єри читаються з БД один раз при старті."""
        with connect_postgres() as conn:
            self.restaurants = conn.execute("SELECT id, city FROM restaurants").fetchall()
            for cid, city in conn.execute("SELECT id, city FROM customers"):
                self.customers[city].append(cid)
            for cid, city in conn.execute("SELECT id, city FROM couriers"):
                self.couriers[city].append(cid)
        log.info(
            "довідники завантажено",
            extra={"fields": {"restaurants": len(self.restaurants),
                              "customers": sum(map(len, self.customers.values()))}},
        )

    def rate_now(self) -> float:
        # хвиля навантаження з періодом 10 хв, щоб на графіках було видно "години пік"
        phase = (time.monotonic() - self.started) / 600 * 2 * math.pi
        return ORDERS_PER_MINUTE * (1 + 0.6 * math.sin(phase))

    def publish(self, event: dict):
        with tracer.start_as_current_span(f"publish {event['type']}", kind=SpanKind.PRODUCER) as span:
            span.set_attribute("messaging.system", "rabbitmq")
            span.set_attribute("messaging.destination.name", QUEUE)
            span.set_attribute("order.id", event["order_id"])

            headers: dict[str, str] = {}
            propagate.inject(headers)  # контекст трейсу їде разом із повідомленням
            self.channel.basic_publish(
                exchange="",
                routing_key=QUEUE,
                body=json.dumps(event, ensure_ascii=False).encode(),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                    headers=headers,
                ),
            )
        events_published.labels(event["status"]).inc()

    def create_order(self):
        restaurant_id, city = random.choice(self.restaurants)
        customer_id = random.choice(self.customers.get(city) or [1])
        order_id = str(uuid.uuid4())

        if random.random() < BROKEN_ORDER_CHANCE:
            # неіснуючий ресторан - worker отримає помилку зовнішнього ключа
            restaurant_id = 9999

        self.publish({
            "type": "order_created",
            "order_id": order_id,
            "status": "created",
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "amount": round(random.uniform(120, 1800), 2),
            "ts": time.time(),
        })
        orders_created.labels(city).inc()
        log.info("нове замовлення", extra={"fields": {"order": order_id, "city": city}})

        if restaurant_id != 9999:
            lo, hi = STAGES["created"][1:]
            self.active.append(ActiveOrder(order_id, city, "created", time.monotonic() + random.uniform(lo, hi)))

    def advance(self, order: ActiveOrder):
        if order.status == "cooking" and random.random() < CANCEL_CHANCE:
            new_status = "cancelled"
        else:
            new_status = STAGES[order.status][0]

        event = {"type": "status_changed", "order_id": order.id, "status": new_status, "ts": time.time()}
        if new_status == "on_the_way":
            event["courier_id"] = random.choice(self.couriers.get(order.city) or [1])
        self.publish(event)

        if new_status in STAGES:
            lo, hi = STAGES[new_status][1:]
            order.status = new_status
            order.due = time.monotonic() + random.uniform(lo, hi)
        else:
            self.active.remove(order)

    def run(self):
        self.load_reference_data()
        log.info("генератор запущено", extra={"fields": {"orders_per_minute": ORDERS_PER_MINUTE}})
        while True:
            rate = self.rate_now()
            current_rate.set(rate)
            if random.random() < rate / 60 * TICK:
                self.create_order()

            now = time.monotonic()
            for order in [o for o in self.active if o.due <= now]:
                self.advance(order)

            orders_in_flight.set(len(self.active))
            # sleep через pika, щоб з'єднання встигало обробляти heartbeat
            self.connection.sleep(TICK)


if __name__ == "__main__":
    start_http_server(8000)
    Generator().run()
