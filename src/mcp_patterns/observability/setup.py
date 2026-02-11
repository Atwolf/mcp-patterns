from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

from mcp_patterns.observability.config import TelemetryConfig

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)


def configure_telemetry(
    config: TelemetryConfig | None = None,
) -> TracerProvider | None:
    """Set up the OpenTelemetry tracing pipeline for an MCP server.

    Attempts to use ``arize-phoenix-otel`` ``register()`` when the package is
    installed and ``use_phoenix_register`` is True.  Falls back to manual OTel
    SDK configuration otherwise.

    Returns the configured ``TracerProvider``, or ``None`` if telemetry is
    disabled or setup fails (in which case tracing degrades to no-ops).
    """
    if config is None:
        config = TelemetryConfig()
    config = config.resolve()

    if not config.enabled:
        logger.info("MCP telemetry disabled (MCP_OTEL_ENABLED=false)")
        return None

    provider: TracerProvider | None = None

    try:
        if config.use_phoenix_register:
            provider = _try_phoenix_register(config)

        if provider is None:
            provider = _setup_manual_otel(config)

        if provider is not None:
            trace.set_tracer_provider(provider)
            _auto_instrument(config, provider)

    except Exception:
        logger.exception(
            "Failed to configure OTel telemetry; tracing will be no-op"
        )
        return None

    return provider


def _try_phoenix_register(config: TelemetryConfig) -> TracerProvider | None:
    try:
        from phoenix.otel import register  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("arize-phoenix-otel not installed; skipping register()")
        return None

    endpoint = config.phoenix_endpoint or config.otlp_endpoint
    if endpoint is None:
        logger.debug("No Phoenix/OTLP endpoint configured; skipping Phoenix register()")
        return None

    kwargs: dict = {
        "project_name": config.phoenix_project_name,
        "endpoint": endpoint,
        "batch": config.batch,
        "set_global_tracer_provider": False,
    }
    if config.phoenix_api_key:
        kwargs["headers"] = {"Authorization": f"Bearer {config.phoenix_api_key}"}

    logger.info("Configuring telemetry via phoenix.otel.register(endpoint=%s)", endpoint)
    return register(**kwargs)  # type: ignore[return-value]


def _setup_manual_otel(config: TelemetryConfig) -> TracerProvider | None:
    endpoint = config.otlp_endpoint or config.phoenix_endpoint
    if endpoint is None:
        logger.info("No OTLP endpoint configured; tracing will be no-op")
        return None

    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": config.service_name})
    provider = _TracerProvider(resource=resource)

    processor = _build_processor(config, endpoint)
    provider.add_span_processor(processor)

    logger.info("Configuring telemetry via manual OTel SDK (endpoint=%s)", endpoint)
    return provider


def _build_processor(config: TelemetryConfig, endpoint: str):
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SimpleSpanProcessor,
    )

    exporter = _build_exporter(config, endpoint)
    if config.batch:
        return BatchSpanProcessor(exporter)
    return SimpleSpanProcessor(exporter)


def _build_exporter(config: TelemetryConfig, endpoint: str):
    headers = dict(config.otlp_headers)
    if config.phoenix_api_key:
        headers["Authorization"] = f"Bearer {config.phoenix_api_key}"

    if config.otlp_protocol == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=endpoint, headers=headers or None)

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    return OTLPSpanExporter(endpoint=endpoint, headers=headers or None)


def _auto_instrument(config: TelemetryConfig, provider: TracerProvider) -> None:
    if config.instrument_httpx:
        try:
            from opentelemetry.instrumentation.httpx import (  # type: ignore[import-untyped]
                HTTPXClientInstrumentor,
            )

            HTTPXClientInstrumentor().instrument(tracer_provider=provider)
            logger.debug("httpx auto-instrumentation enabled")
        except ImportError:
            logger.debug(
                "opentelemetry-instrumentation-httpx not installed; skipping"
            )

    if config.instrument_mcp_context:
        try:
            from openinference.instrumentation.mcp import (  # type: ignore[import-untyped]
                MCPInstrumentor,
            )

            MCPInstrumentor().instrument(tracer_provider=provider)
            logger.debug("MCP context propagation instrumentation enabled")
        except ImportError:
            logger.debug(
                "openinference-instrumentation-mcp not installed; skipping"
            )
