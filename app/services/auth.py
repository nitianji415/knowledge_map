"""认证服务:密码 hash、JWT 签发/校验、FastAPI 当前用户依赖。

依赖原则:
  - bcrypt 直接调用,不走 passlib —— passlib 1.7.x 在 bcrypt 4.3+ 上 init 期就崩
  - PyJWT:HS256 token,7-14 天有效
  - get_current_user 是 FastAPI Depends,Bearer token 通过就放行
  - AUTH_ENABLED=false 时 (仅供 pytest) 绕过验证,自动返回 admin 用户

bcrypt 硬限制 72 字节,这里统一在入口 truncate,避免业务层踩坑。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.base import get_session
from app.db.models import AppUser, now_utc


def _truncate_for_bcrypt(plain: str) -> bytes:
    """bcrypt 严格只接受 ≤72 字节,超出直接报错。统一在 hash/verify 入口处 truncate。"""
    return plain.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    hashed = bcrypt.hashpw(_truncate_for_bcrypt(plain), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_truncate_for_bcrypt(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expires_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 已过期,请重新登录",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 无效",
        ) from exc


async def get_user_by_id(db: AsyncSession, user_id: str) -> AppUser | None:
    return await db.get(AppUser, user_id)


async def get_user_by_username(db: AsyncSession, username: str) -> AppUser | None:
    result = await db.execute(select(AppUser).where(AppUser.username == username))
    return result.scalars().first()


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization 头格式错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> AppUser:
    """FastAPI 依赖:Bearer token → AppUser。

    AUTH_ENABLED=false (仅 pytest) 时返回固定的 local_user。
    """
    settings = get_settings()
    if not settings.auth_enabled:
        user = await get_user_by_id(db, "local_user")
        if not user:
            # 测试场景下 alembic seed 没跑,自己 inject 一个
            user = AppUser(
                id="local_user",
                username="admin",
                password_hash="",
                role="admin",
                must_change_password=False,
            )
            db.add(user)
            await db.flush()
        return user

    token = _extract_bearer(authorization)
    payload = decode_token(token, settings)
    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token 无 sub"
        )
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


async def require_admin(user: AppUser = Depends(get_current_user)) -> AppUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要 admin 权限")
    return user


async def ensure_admin_seeded(db: AsyncSession, settings: Settings | None = None) -> AppUser:
    """启动时调用:确保至少有一个 admin,密码用 ADMIN_PASSWORD。

    幂等:
      - 没有 local_user 行 → 建一个
      - 有但 password_hash 为空(刚 migrate 出来的占位)→ 写入 hash
      - 有且 hash 不空 → 不动,尊重用户已经改过的密码
    """
    settings = settings or get_settings()
    user = await get_user_by_id(db, "local_user")
    is_default_password = settings.admin_password == "admin"

    if user is None:
        user = AppUser(
            id="local_user",
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
            must_change_password=is_default_password,
        )
        db.add(user)
    elif not user.password_hash:
        # 占位行(0007 migrate 刚跑出来),写真 hash
        user.username = settings.admin_username
        user.password_hash = hash_password(settings.admin_password)
        user.role = "admin"
        user.must_change_password = is_default_password
        user.updated_at = now_utc()
    # else: 已经有真密码了,不动

    if is_default_password:
        print(
            "[knowledge_map] ⚠️  ADMIN_PASSWORD 还是默认 'admin' —— "
            "登录后请立刻去设置页修改"
        )
    return user
