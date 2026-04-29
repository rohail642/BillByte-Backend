from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    payload = decode_token(token)
    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    from app.models.user import User
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def require_owner(current_user=Depends(get_current_user)):
    if current_user.role not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return current_user


# ── RBAC ──────────────────────────────────────────────────────────────────────
ROLE_HIERARCHY = {"owner": 4, "manager": 3, "cashier": 2, "waiter": 1}

def require_role(*allowed_roles):
    """Dependency that checks user has one of the allowed roles."""
    async def checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required roles: {', '.join(allowed_roles)}"
            )
        return current_user
    return checker

def require_min_role(min_role: str):
    """Dependency that checks user has at least the minimum role level."""
    min_level = ROLE_HIERARCHY.get(min_role, 0)
    async def checker(current_user: User = Depends(get_current_user)):
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Requires {min_role} or higher."
            )
        return current_user
    return checker

async def require_super_admin(current_user: User = Depends(get_current_user)):
    """Only super_admin accounts can access admin routes."""
    if current_user.role != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Access denied. Super admin only."
        )
    return current_user