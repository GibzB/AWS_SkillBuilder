"""Configuration-driven Publishing_Adapter selection.

Exactly one publishing implementation must be selected through configuration;
selecting zero or more than one is a configuration error that blocks any
publish attempt (Requirements 6.4, 6.5). The MVP default is ``mock``
(Publishing Adapter Strategy), which keeps the pipeline runnable and under
budget while the real publisher remains a blocked discovery task. The
``builder_center`` option is an explicit, opt-in choice and is never selected
implicitly.

The registry maps a configuration token to a zero-argument factory, so the
selection logic stays independent of how each publisher is constructed.

Design reference: design.md "Publishing Adapter Strategy"; Requirements 6.4,
6.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.common.errors import ConfigurationError
from app.publishing.adapter import PublishingAdapter
from app.publishing.builder_center import BuilderCenterPublisher
from app.publishing.mock import MockPublisher
from app.publishing.s3_publisher import S3Client, S3Publisher

__all__ = [
    "MOCK",
    "BUILDER_CENTER",
    "S3",
    "DEFAULT_PUBLISHER_SELECTION",
    "KNOWN_SELECTIONS",
    "PublisherConfig",
    "select_publisher",
]

MOCK: Final[str] = "mock"
BUILDER_CENTER: Final[str] = "builder_center"
S3: Final[str] = "s3"

# The MVP default keeps the system on the MockPublisher.
DEFAULT_PUBLISHER_SELECTION: Final[str] = MOCK

# All recognized selection tokens.
KNOWN_SELECTIONS: Final[tuple[str, ...]] = (MOCK, BUILDER_CENTER, S3)


@dataclass(frozen=True)
class PublisherConfig:
    """Dependencies some publishers need at construction.

    ``mock`` and ``builder_center`` need none; ``s3`` needs an S3 client, the
    target bucket, and the CloudFront base URL. Supplied by the Lambda handler
    from environment configuration.
    """

    s3_client: S3Client | None = None
    s3_bucket: str = ""
    s3_base_url: str = ""


def select_publisher(
    selection: str, config: PublisherConfig | None = None
) -> PublishingAdapter:
    """Return the configured Publishing_Adapter, or raise on invalid selection.

    ``selection`` must name exactly one known implementation. An empty value
    (zero implementations) or an unknown token is a :class:`ConfigurationError`
    that blocks any publish attempt (Requirement 6.5). Matched case-insensitively
    and trimmed. The ``s3`` publisher additionally requires a complete
    :class:`PublisherConfig` (client, bucket, base URL); missing S3 config is a
    configuration error that blocks publishing.
    """
    normalized = selection.strip().casefold()
    cfg = config or PublisherConfig()

    if not normalized:
        raise ConfigurationError(
            message=(
                "no publishing implementation selected; exactly one is "
                f"required (one of: {', '.join(KNOWN_SELECTIONS)})"
            ),
            fields=("publisher_selection",),
        )
    if normalized == MOCK:
        return MockPublisher()
    if normalized == BUILDER_CENTER:
        return BuilderCenterPublisher()
    if normalized == S3:
        if cfg.s3_client is None or not cfg.s3_bucket or not cfg.s3_base_url:
            missing = [
                name
                for name, present in (
                    ("s3_client", cfg.s3_client is not None),
                    ("s3_bucket", bool(cfg.s3_bucket)),
                    ("s3_base_url", bool(cfg.s3_base_url)),
                )
                if not present
            ]
            raise ConfigurationError(
                message=(
                    "s3 publisher selected but required S3 configuration is "
                    f"missing: {', '.join(missing)}"
                ),
                fields=tuple(missing),
            )
        return S3Publisher(cfg.s3_client, cfg.s3_bucket, cfg.s3_base_url)

    raise ConfigurationError(
        message=(
            f"unknown publisher selection '{selection}'; expected exactly "
            f"one of: {', '.join(KNOWN_SELECTIONS)}"
        ),
        fields=("publisher_selection",),
    )
