"""Repositories: the only place that builds queries. Event repositories can only append."""

from caspian_parking.data.repositories.base import EventRepository, ReferenceRepository, StaleRecordError

__all__ = ["EventRepository", "ReferenceRepository", "StaleRecordError"]
