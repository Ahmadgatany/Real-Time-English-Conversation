from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, db_session
from app.api.schemas import AuthInput, LoginInput, PreferencesInput, UserOutput
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 60 * 24 * 7,
    )


@router.post("/signup", response_model=UserOutput, status_code=status.HTTP_201_CREATED)
async def signup(payload: AuthInput, request: Request, response: Response, session: AsyncSession = Depends(db_session)) -> User:
    email = str(payload.email).lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip() or email.split("@", 1)[0],
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    set_session_cookie(response, create_access_token(user.id, request.app.state.settings), request.app.state.settings.secure_cookies)
    return user


@router.post("/login", response_model=UserOutput)
async def login(payload: LoginInput, request: Request, response: Response, session: AsyncSession = Depends(db_session)) -> User:
    user = await session.scalar(select(User).where(User.email == str(payload.email).lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    set_session_cookie(response, create_access_token(user.id, request.app.state.settings), request.app.state.settings.secure_cookies)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie("access_token")


@router.get("/me", response_model=UserOutput)
async def me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOutput)
async def update_profile(
    payload: PreferencesInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> User:
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or user.display_name
    user.preferences = payload.model_dump()
    user.preferences.pop("display_name", None)
    await session.commit()
    await session.refresh(user)
    return user
