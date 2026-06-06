"""认证路由:登录、当前用户、改密码。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AppUser, now_utc
from app.schemas import ChangePasswordIn, ChangePasswordOut, LoginIn, LoginOut, UserOut
from app.services.auth import (
    create_access_token,
    get_current_user,
    get_user_by_username,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginOut)
async def login(
    payload: LoginIn,
    db: AsyncSession = Depends(get_session),
) -> LoginOut:
    user = await get_user_by_username(db, payload.username)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token = create_access_token(user.id)
    return LoginOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: AppUser = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/change-password", response_model=ChangePasswordOut)
async def change_password(
    payload: ChangePasswordIn,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ChangePasswordOut:
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="旧密码不正确",
        )
    if payload.new_password == payload.old_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能和旧密码相同",
        )
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.updated_at = now_utc()
    await db.flush()
    return ChangePasswordOut(user=UserOut.model_validate(user))
