"""Blocklist (SPEC §4.8): per plate or per person, alarm on arrival, attempts log, unblock with reason."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from caspian_parking.core.permissions import Permission
from caspian_parking.core.plate import Plate
from caspian_parking.data.models import Block, BlockAttempt, PersonPlate
from caspian_parking.data.repositories.base import ReferenceRepository
from caspian_parking.services.context import AppContext

CATEGORIES = ("debtor", "security", "harassment", "other")
GENERIC_CATEGORIES = frozenset({"security"})  # operators only see "entry forbidden — call the supervisor"


class BlockError(RuntimeError):
    pass


class BlockRepository(ReferenceRepository[Block]):
    model = Block


@dataclass(frozen=True)
class BlockMatch:
    block: Block
    generic: bool  # show only the generic message to this user


class BlocklistService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def _require(self) -> None:
        if not self.ctx.can(Permission.MANAGE_BLOCKLIST):
            raise BlockError("gate.permission_denied")

    def block_plate(self, plate: Plate, category: str, description: str) -> Block:
        self._require()
        if category not in CATEGORIES:
            raise BlockError("block.bad_category")
        with self.ctx.uow(reason=description) as session:
            return BlockRepository(session).add(Block(plate_key=plate.key, category=category, description=description))

    def block_person(self, person_id: str, category: str, description: str) -> Block:
        self._require()
        if category not in CATEGORIES:
            raise BlockError("block.bad_category")
        with self.ctx.uow(reason=description) as session:
            return BlockRepository(session).add(Block(person_id=person_id, category=category, description=description))

    def unblock(self, block_id: str, reason: str) -> None:
        self._require()
        if not reason.strip():
            raise BlockError("gate.reason_required")
        with self.ctx.uow(reason=reason) as session:
            block = session.get(Block, block_id)
            if block is None:
                raise BlockError("block.not_found")
            BlockRepository(session).deactivate(block, reason)

    def active(self, offset: int = 0, limit: int = 200) -> list[Block]:
        with self.ctx.read() as session:
            stmt = select(Block).where(Block.is_active.is_(True)).order_by(Block.created_at_utc.desc())
            return list(session.scalars(stmt.offset(offset).limit(limit)))

    def match(self, session: Session, plate_key: str) -> BlockMatch | None:
        """Active block for this plate, directly or through the person who owns the plate."""
        owners = select(PersonPlate.person_id).where(
            PersonPlate.plate_key == plate_key, PersonPlate.is_active.is_(True)
        )
        block = session.scalar(
            select(Block)
            .where(Block.is_active.is_(True), or_(Block.plate_key == plate_key, Block.person_id.in_(owners)))
            .order_by(Block.created_at_utc.desc())
        )
        if block is None:
            return None
        generic = block.category in GENERIC_CATEGORIES and not self.ctx.can(Permission.VIEW_BLOCK_DETAILS)
        return BlockMatch(block, generic)

    def record_attempt(self, block: Block, plate_key: str | None, gate_code: int | None) -> BlockAttempt:
        with self.ctx.uow() as session:
            attempt = BlockAttempt(block_id=block.id, plate_key=plate_key, gate_code=gate_code, details={})
            session.add(attempt)
        return attempt

    def attempts(self, block_id: str | None = None, limit: int = 200) -> list[BlockAttempt]:
        with self.ctx.read() as session:
            stmt = select(BlockAttempt).order_by(BlockAttempt.created_at_utc.desc()).limit(limit)
            if block_id:
                stmt = stmt.where(BlockAttempt.block_id == block_id)
            return list(session.scalars(stmt))
