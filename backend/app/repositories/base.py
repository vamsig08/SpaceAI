"""Abstract base repository providing common CRUD operations.

All concrete repositories inherit from this base and can override
or extend these methods for entity-specific queries.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Generic async repository with standard CRUD operations.

    Provides:
    - get_by_id: Fetch single record by primary key
    - list: Paginated listing with optional ordering
    - count: Count records with optional filter
    - create: Insert a single record
    - create_many: Bulk insert
    - update: Update fields on an existing record
    - delete: Remove a record by ID

    Args:
        model: The SQLAlchemy model class this repository manages.
        session: The async database session.
    """

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def get_by_id(self, entity_id: str) -> ModelT | None:
        """Fetch a single record by primary key.

        Args:
            entity_id: UUID string of the record.

        Returns:
            The model instance or None if not found.
        """
        return await self._session.get(self._model, entity_id)

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        order_by: str | None = None,
        order_desc: bool = True,
        filters: list[Any] | None = None,
    ) -> list[ModelT]:
        """Fetch a paginated list of records.

        Args:
            offset: Number of records to skip.
            limit: Maximum records to return.
            order_by: Column name to sort by.
            order_desc: Sort descending if True.
            filters: List of SQLAlchemy filter expressions.

        Returns:
            List of model instances.
        """
        stmt = select(self._model)

        if filters:
            for f in filters:
                stmt = stmt.where(f)

        if order_by and hasattr(self._model, order_by):
            col = getattr(self._model, order_by)
            stmt = stmt.order_by(col.desc() if order_desc else col.asc())

        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, filters: list[Any] | None = None) -> int:
        """Count records matching optional filters.

        Args:
            filters: List of SQLAlchemy filter expressions.

        Returns:
            Total count of matching records.
        """
        stmt = select(func.count()).select_from(self._model)
        if filters:
            for f in filters:
                stmt = stmt.where(f)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def create(self, entity: ModelT) -> ModelT:
        """Insert a single record.

        Args:
            entity: Model instance to persist.

        Returns:
            The persisted model instance.
        """
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def create_many(self, entities: list[ModelT]) -> list[ModelT]:
        """Bulk insert multiple records.

        Args:
            entities: List of model instances to persist.

        Returns:
            The persisted model instances.
        """
        self._session.add_all(entities)
        await self._session.flush()
        return entities

    async def update(self, entity_id: str, **kwargs: Any) -> ModelT | None:
        """Update specific fields on an existing record.

        Args:
            entity_id: UUID of the record to update.
            **kwargs: Field names and new values.

        Returns:
            Updated model instance or None if not found.
        """
        entity = await self.get_by_id(entity_id)
        if entity is None:
            return None
        for key, value in kwargs.items():
            if hasattr(entity, key):
                setattr(entity, key, value)
        await self._session.flush()
        return entity

    async def delete(self, entity_id: str) -> bool:
        """Remove a record by ID.

        Args:
            entity_id: UUID of the record to delete.

        Returns:
            True if deleted, False if not found.
        """
        entity = await self.get_by_id(entity_id)
        if entity is None:
            return False
        await self._session.delete(entity)
        await self._session.flush()
        return True
