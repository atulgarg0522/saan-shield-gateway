import secrets
import string
import uuid
from datetime import datetime, timezone
from typing import Tuple, List, Optional
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.api_key import ApiKey


def _generate_random_string(length: int) -> str:
    """
    Generates a secure random alphanumeric string.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def validate_api_key(raw_key: str, db: AsyncSession) -> Optional[ApiKey]:
    """
    Authenticates a gateway client API key.
    1. Extracts key_prefix (first 8 characters, e.g. 'nim_abc1').
    2. Queries active keys matching the prefix.
    3. Verifies key integrity using bcrypt.
    4. Audits expiration parameters.
    5. Updates last_used_at upon success.
    """
    if not raw_key or len(raw_key) < 10 or not raw_key.startswith("nim_"):
        return None

    # First 8 characters are the prefix e.g. nim_abc1
    prefix = raw_key[:8]

    # Query active candidate keys matching the prefix (hits prefix index)
    stmt = select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.is_active == True)
    result = await db.execute(stmt)
    candidate_keys = result.scalars().all()

    for key in candidate_keys:
        # Check secure bcrypt hash
        if bcrypt.checkpw(raw_key.encode('utf-8'), key.key_hash.encode('utf-8')):
            # Validate expiration limit
            if key.expires_at and key.expires_at < datetime.now(timezone.utc):
                continue
            
            # Log usage asynchronously
            key.last_used_at = datetime.now(timezone.utc)
            await db.commit()
            return key

    return None


async def create_api_key(
    org_id: uuid.UUID,
    name: str,
    scopes: List[str],
    db: AsyncSession,
    expires_at: Optional[datetime] = None
) -> Tuple[ApiKey, str]:
    """
    Generates a new secure random API key for an organization.
    Format: nim_<4_chars_prefix>_<32_chars_secret>
    Bcrypt hashes the key and saves to database, returning the ORM model and plaintext key.
    """
    # 4 random characters to append to 'nim_' making 8 chars total prefix (e.g. nim_abc1)
    prefix_rand = _generate_random_string(4).lower()
    prefix = f"nim_{prefix_rand}"
    
    # 32 random characters for the secure secret payload
    secret = _generate_random_string(32)
    raw_key = f"{prefix}_{secret}"
    
    # Hash the complete raw key
    salt = bcrypt.gensalt()
    key_hash = bcrypt.hashpw(raw_key.encode('utf-8'), salt).decode('utf-8')

    db_key = ApiKey(
        org_id=org_id,
        name=name,
        key_hash=key_hash,
        key_prefix=prefix,
        scopes=scopes,
        is_active=True,
        expires_at=expires_at
    )
    
    db.add(db_key)
    await db.commit()
    await db.refresh(db_key)
    
    return db_key, raw_key
