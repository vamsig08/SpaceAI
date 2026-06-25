"""Audit logging service — immutable record of all system actions.

Every significant operation flows through this service to create
a permanent, queryable audit trail. Audit records are never modified
or deleted.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now

logger = get_logger(__name__)


class AuditService:
    """Creates and queries immutable audit log entries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        action: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        bytes_affected: int = 0,
        paths_affected: list[str] | None = None,
        severity: str = "info",
        correlation_id: str | None = None,
    ) -> str:
        """Create an audit log entry.

        Args:
            action: Action identifier (e.g. file_trashed, cleanup_executed).
            entity_type: Type of affected entity (file, scan, cleanup_action).
            entity_id: ID of the affected entity.
            description: Human-readable description.
            metadata: JSON-serializable action-specific details.
            bytes_affected: Total bytes involved in the operation.
            paths_affected: List of file paths affected.
            severity: Log severity (info|warning|critical).
            correlation_id: Links related events together.

        Returns:
            The generated audit log ID.
        """
        log_id = generate_uuid()
        now = utc_now()

        await self._session.execute(
            text(
                """
                INSERT INTO audit_logs (
                    id, correlation_id, action, entity_type, entity_id,
                    description, metadata, bytes_affected, paths_affected,
                    severity, created_at
                ) VALUES (
                    :id, :corr, :action, :etype, :eid,
                    :desc, :meta, :bytes, :paths, :severity, :now
                )
                """
            ),
            {
                "id": log_id,
                "corr": correlation_id,
                "action": action,
                "etype": entity_type,
                "eid": entity_id,
                "desc": description,
                "meta": json.dumps(metadata) if metadata else None,
                "bytes": bytes_affected,
                "paths": json.dumps(paths_affected[:50]) if paths_affected else None,
                "severity": severity,
                "now": now,
            },
        )
        await self._session.flush()
        return log_id

    async def get_logs(
        self,
        *,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        correlation_id: str | None = None,
        severity: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Query audit logs with optional filters.

        Returns:
            Dict with logs list and pagination metadata.
        """
        conditions = ["1=1"]
        params: dict[str, Any] = {}

        if action:
            conditions.append("action = :action")
            params["action"] = action
        if entity_type:
            conditions.append("entity_type = :etype")
            params["etype"] = entity_type
        if entity_id:
            conditions.append("entity_id = :eid")
            params["eid"] = entity_id
        if correlation_id:
            conditions.append("correlation_id = :corr")
            params["corr"] = correlation_id
        if severity:
            conditions.append("severity = :severity")
            params["severity"] = severity

        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        count_result = await self._session.execute(
            text(f"SELECT COUNT(*) FROM audit_logs WHERE {where}"), params
        )
        total = count_result.scalar_one()

        params["limit"] = page_size
        params["offset"] = offset
        result = await self._session.execute(
            text(
                f"""
                SELECT id, correlation_id, action, entity_type, entity_id,
                       description, metadata, bytes_affected, paths_affected,
                       severity, created_at
                FROM audit_logs WHERE {where}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )

        logs = [
            {
                "id": row[0],
                "correlation_id": row[1],
                "action": row[2],
                "entity_type": row[3],
                "entity_id": row[4],
                "description": row[5],
                "metadata": json.loads(row[6]) if row[6] else None,
                "bytes_affected": row[7],
                "paths_affected": json.loads(row[8]) if row[8] else None,
                "severity": row[9],
                "created_at": row[10],
            }
            for row in result.all()
        ]

        return {
            "logs": logs,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        }
