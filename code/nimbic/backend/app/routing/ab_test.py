import json
import uuid
import hashlib
from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, Literal
from sqlalchemy import select

from app.db.session import async_session_local
from app.models.ab_test import ABTest as ABTestModel, ABTestResult as ABTestResultModel

@dataclass
class ABTest:
    id: uuid.UUID
    org_id: str
    name: str
    model_a: str
    provider_a: str
    model_b: str
    provider_b: str
    split_pct: int
    status: Literal["active", "paused", "completed"]
    started_at: datetime
    test_mode: str = "traffic_split"
    ends_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "name": self.name,
            "model_a": self.model_a,
            "provider_a": self.provider_a,
            "model_b": self.model_b,
            "provider_b": self.provider_b,
            "split_pct": self.split_pct,
            "test_mode": self.test_mode,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ABTest":
        return cls(
            id=uuid.UUID(d["id"]),
            org_id=d["org_id"],
            name=d["name"],
            model_a=d["model_a"],
            provider_a=d["provider_a"],
            model_b=d["model_b"],
            provider_b=d["provider_b"],
            split_pct=d["split_pct"],
            test_mode=d.get("test_mode", "traffic_split"),
            status=d["status"],
            started_at=datetime.fromisoformat(d["started_at"]) if d["started_at"] else None,
            ends_at=datetime.fromisoformat(d["ends_at"]) if d["ends_at"] else None,
        )


class ABTestManager:
    async def get_active_test(self, org_id: str, redis) -> Optional[ABTest]:
        """
        Retrieves the active A/B test for the organization.
        Results are cached in Redis for 30 seconds.
        """
        redis_key = f"ab_test:active:{org_id}"
        
        # 1. Try Cache Lookup
        try:
            cached = await redis.get(redis_key)
            if cached:
                val = cached.decode("utf-8") if isinstance(cached, bytes) else cached
                if val == "none":
                    return None
                return ABTest.from_dict(json.loads(val))
        except Exception:
            pass

        # 2. Database Lookup
        async with async_session_local() as db:
            try:
                org_uuid = uuid.UUID(org_id)
            except ValueError:
                return None
                
            stmt = select(ABTestModel).where(
                ABTestModel.org_id == org_uuid,
                ABTestModel.status == "active"
            )
            result = await db.execute(stmt)
            test_db = result.scalars().first()

            if test_db:
                # Map to dataclass
                # Ensure we handle dates timezone info
                started_at = test_db.started_at
                ends_at = test_db.ends_at
                
                test_dc = ABTest(
                    id=test_db.id,
                    org_id=str(test_db.org_id),
                    name=test_db.name,
                    model_a=test_db.model_a,
                    provider_a=test_db.provider_a,
                    model_b=test_db.model_b,
                    provider_b=test_db.provider_b,
                    split_pct=test_db.split_pct,
                    test_mode=getattr(test_db, 'test_mode', 'traffic_split'),
                    status=test_db.status,
                    started_at=started_at,
                    ends_at=ends_at
                )
                
                # Cache in Redis with 30s TTL
                try:
                    await redis.setex(redis_key, 30, json.dumps(test_dc.to_dict()))
                except Exception:
                    pass
                return test_dc
            else:
                # Cache negative hit
                try:
                    await redis.setex(redis_key, 30, "none")
                except Exception:
                    pass
                return None

    async def assign_variant(self, request_id: str, test: ABTest) -> str:
        """
        Deterministically assigns Variant "A" or "B" based on request_id and test.id.
        Uses MD5 hashing for cross-process stability.
        """
        combined = f"{request_id}{test.id}"
        h = hashlib.md5(combined.encode("utf-8")).hexdigest()
        val = int(h, 16) % 100
        if val < test.split_pct:
            return "B"
        return "A"

    async def record_result(self, request_id: str, variant: str, cost: Decimal, latency: int, org_id: str, db) -> None:
        """
        Records the outcome of a request routed through an active A/B test.
        """
        try:
            org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
        except ValueError:
            return

        # Query active test first to associate with test_id
        stmt = select(ABTestModel).where(
            ABTestModel.org_id == org_uuid,
            ABTestModel.status == "active"
        )
        result = await db.execute(stmt)
        active_test = result.scalars().first()

        if active_test:
            res_record = ABTestResultModel(
                test_id=active_test.id,
                org_id=org_uuid,
                request_id=request_id,
                variant=variant,
                cost=cost,
                latency=latency
            )
            db.add(res_record)
            await db.commit()
