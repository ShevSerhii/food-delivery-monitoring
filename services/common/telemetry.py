"""Логування у форматі logfmt і налаштування трейсингу (OpenTelemetry -> Tempo)."""

import logging
import os
import sys
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class LogfmtFormatter(logging.Formatter):
    """Рядок виду: ts=... level=info service=worker msg="..." trace_id=...

    Promtail розбирає такі рядки без додаткових регулярок, а trace_id
    дозволяє з Loki одразу перейти до трейсу в Tempo.
    """

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        msg = record.getMessage().replace('"', "'")
        parts = [
            f"ts={ts}.{int(record.msecs):03d}Z",
            f"level={record.levelname.lower()}",
            f"service={self.service}",
            f'msg="{msg}"',
        ]
        for key, value in getattr(record, "fields", {}).items():
            parts.append(f"{key}={value}")

        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            parts.append(f"trace_id={ctx.trace_id:032x}")

        if record.exc_info:
            # в один рядок потрапляє лише останній рядок traceback
            last_line = self.formatException(record.exc_info).splitlines()[-1]
            parts.append(f'error="{last_line.replace(chr(34), chr(39))}"')
        return " ".join(parts)


def setup(service: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(LogfmtFormatter(service))
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[handler])
    # pika дублює кожну невдалу спробу підключення кількома рядками ERROR,
    # а повторні спроби ми й так логуємо самі в connections.py
    logging.getLogger("pika").setLevel(logging.CRITICAL)

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(provider)

    return logging.getLogger(service)
