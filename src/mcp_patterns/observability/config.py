from __future__ import annotations

import os

from pydantic import BaseModel, Field


class TelemetryConfig(BaseModel):
    """Configuration for MCP server telemetry and observability."""

    service_name: str = Field(
        default="mcp-server",
        description="OTel service name; used as the primary identifier in traces.",
    )
    enabled: bool = Field(
        default=True,
        description="Master switch for OTel instrumentation.",
    )
    phoenix_endpoint: str | None = Field(
        default=None,
        description=(
            "Phoenix collector endpoint (e.g. http://localhost:6006/v1/traces). "
            "When set, traces are exported here. Falls back to PHOENIX_COLLECTOR_ENDPOINT env var."
        ),
    )
    phoenix_project_name: str = Field(
        default="default",
        description="Project name for trace grouping in Phoenix.",
    )
    phoenix_api_key: str | None = Field(
        default=None,
        description="API key for Phoenix Cloud / Arize Cloud. Falls back to PHOENIX_API_KEY env var.",
    )
    otlp_endpoint: str | None = Field(
        default=None,
        description=(
            "Generic OTLP endpoint (e.g. http://localhost:4317). "
            "Used when phoenix_endpoint is not set. Falls back to OTEL_EXPORTER_OTLP_ENDPOINT env var."
        ),
    )
    otlp_protocol: str = Field(
        default="grpc",
        description="OTLP protocol: 'grpc' or 'http/protobuf'.",
    )
    otlp_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra headers for the OTLP exporter (e.g. auth tokens).",
    )
    capture_tool_io: bool = Field(
        default=False,
        description=(
            "Whether to record tool input/output values in span attributes. "
            "Falls back to MCP_OTEL_CAPTURE_IO env var."
        ),
    )
    log_level: str = Field(
        default="INFO",
        description="Python logging level. Falls back to MCP_LOG_LEVEL env var.",
    )
    batch: bool = Field(
        default=True,
        description="Use BatchSpanProcessor (True) or SimpleSpanProcessor (False).",
    )
    instrument_httpx: bool = Field(
        default=True,
        description="Auto-instrument httpx.AsyncClient calls.",
    )
    instrument_mcp_context: bool = Field(
        default=True,
        description="Enable MCP client-server context propagation via openinference-instrumentation-mcp.",
    )
    use_phoenix_register: bool = Field(
        default=True,
        description=(
            "Use arize-phoenix-otel register() for setup when available. "
            "Falls back to manual OTel SDK setup if the package is not installed."
        ),
    )

    def resolve(self) -> TelemetryConfig:
        """Return a copy with env-var fallbacks applied."""
        return self.model_copy(
            update={
                "enabled": _env_bool("MCP_OTEL_ENABLED", self.enabled),
                "phoenix_endpoint": self.phoenix_endpoint
                or os.getenv("PHOENIX_COLLECTOR_ENDPOINT"),
                "phoenix_project_name": os.getenv(
                    "PHOENIX_PROJECT_NAME", self.phoenix_project_name
                ),
                "phoenix_api_key": self.phoenix_api_key
                or os.getenv("PHOENIX_API_KEY"),
                "otlp_endpoint": self.otlp_endpoint
                or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
                "otlp_protocol": os.getenv(
                    "OTEL_EXPORTER_OTLP_PROTOCOL", self.otlp_protocol
                ),
                "capture_tool_io": _env_bool(
                    "MCP_OTEL_CAPTURE_IO", self.capture_tool_io
                ),
                "log_level": os.getenv("MCP_LOG_LEVEL", self.log_level),
                "service_name": os.getenv(
                    "MCP_OTEL_SERVICE_NAME", self.service_name
                ),
            }
        )


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")
