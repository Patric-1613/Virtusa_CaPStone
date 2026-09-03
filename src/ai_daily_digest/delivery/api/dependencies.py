"""Typed dependency boundaries for the Delivery HTTP API."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast

from fastapi import Request

LOGGER = logging.getLogger(__name__)

_SAFE_DEPENDENCY_NAME = re.compile(r"\A[a-z][a-z0-9_-]{0,63}\Z")


class ReadinessProbe(Protocol):
    """A narrow, infrastructure-independent readiness boundary."""

    async def is_ready(self) -> bool:
        """Return whether the configured dependency can currently serve work."""


@dataclass(frozen=True)
class ReadinessCheck:
    """A safe readiness result suitable for a public response."""

    name: str
    ready: bool


@dataclass(frozen=True)
class ReadinessRegistry:
    """Validated required-dependency configuration and its concrete probes."""

    required_dependencies: tuple[str, ...]
    probes: Mapping[str, ReadinessProbe]

    async def evaluate(self, *, request_id: uuid.UUID) -> tuple[ReadinessCheck, ...]:
        """Evaluate every required probe without exposing its exception details."""
        checks: list[ReadinessCheck] = []
        for name in self.required_dependencies:
            try:
                ready = await self.probes[name].is_ready()
            # A probe may wrap any infrastructure client; every such failure
            # means "not ready" and must be reduced to a safe coarse status.
            except Exception as exc:  # pylint: disable=broad-exception-caught
                LOGGER.error(
                    "Readiness probe raised an exception",
                    extra={
                        "request_id": str(request_id),
                        "dependency": name,
                        "exception_type": type(exc).__name__,
                    },
                )
                ready = False
            checks.append(ReadinessCheck(name=name, ready=ready))
        return tuple(checks)


def build_readiness_registry(
    *,
    required_dependencies: Iterable[str] = (),
    probes: Mapping[str, ReadinessProbe] | None = None,
) -> ReadinessRegistry:
    """Validate readiness configuration before the application serves requests."""
    required = tuple(required_dependencies)
    if len(required) != len(set(required)):
        raise ValueError("required dependency names must be unique")
    for name in required:
        if _SAFE_DEPENDENCY_NAME.fullmatch(name) is None:
            raise ValueError(f"invalid required dependency name: {name!r}")

    configured = dict(probes or {})
    missing = sorted(set(required).difference(configured))
    if missing:
        missing_names = ", ".join(missing)
        raise ValueError(f"required dependencies have no readiness probe: {missing_names}")
    for name in required:
        if not callable(getattr(configured[name], "is_ready", None)):
            raise ValueError(f"readiness probe for {name!r} has no callable is_ready method")

    ordered_required = tuple(sorted(required))
    selected = MappingProxyType({name: configured[name] for name in ordered_required})
    return ReadinessRegistry(required_dependencies=ordered_required, probes=selected)


def get_readiness_registry(request: Request) -> ReadinessRegistry:
    """Resolve the application-scoped readiness registry for a route."""
    return cast(ReadinessRegistry, request.app.state.readiness_registry)


def get_request_id(request: Request) -> uuid.UUID:
    """Return the UUID v7 allocated once by request middleware."""
    return cast(uuid.UUID, request.state.request_id)
