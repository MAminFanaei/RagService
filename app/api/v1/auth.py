# app/api/v1/auth.py
"""
Authentication Endpoints - Async Version
"""

from fastapi import APIRouter, Depends, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.services.credit_service import CreditService
from app.core.database import get_db, get_redis
from app.core.security import create_token_pair, decode_token, oauth, blacklist_token
from app.schemas.auth import Token, RefreshTokenRequest, OAuthCallbackResponse
from app.services.user_service import UserService
from app.services.rate_limit_service import RateLimitService
from app.models.user import AuthProvider, User
from app.api.deps import get_current_user, get_redis_client
from app.config import settings
from app.core.feature_flags import require_feature
from app.middleware.exceptions import BadRequestException, InternalException, NotImplementedException, UnauthorizedException
from app.schemas.user import (
    UserCreate, UserLogin, UserResponse,
    PasswordChangeRequest, EmailChangeRequest, ProfileUpdateRequest,
    PasswordChangeResponse, EmailChangeResponse, ProfileUpdateResponse
)

security = HTTPBearer()
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
@require_feature(flag_name="ENABLE_REGISTRATION", disabled_message="Registration is disabled")
async def register(request: Request, user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    if user_data.email:
        existing_user = await UserService.get_by_email(db, user_data.email)
        if existing_user:
            raise BadRequestException("Email already registered")
    
    if user_data.username:
        existing_username = await UserService.get_by_username(db, user_data.username)
        if existing_username:
            raise BadRequestException("Username already taken")
    
    user = await UserService.create_user(db, user_data)
    return create_token_pair(user_id=user.id, email=user.email, username=user.username, is_admin=user.is_admin)


@router.post("/login", response_model=Token)
async def login(request: Request, credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    user = await UserService.authenticate(db, credentials.login, credentials.password)
    if not user:
        raise UnauthorizedException("Incorrect email/username or password")
    return create_token_pair(user_id=user.id, email=user.email, username=user.username, is_admin=user.is_admin)


@router.post("/refresh", response_model=Token)
async def refresh_token(token_request: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(token_request.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise UnauthorizedException("Invalid refresh token")
    
    user = await UserService.get_by_id(db, payload.get("sub"))
    if not user or not user.is_active:
        raise UnauthorizedException("User not found or inactive")
    
    return create_token_pair(user_id=user.id, email=user.email, username=user.username, is_admin=user.is_admin)


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    redis_client: aioredis.Redis = Depends(get_redis)
):
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise UnauthorizedException("Invalid token")
    
    success = await blacklist_token(redis_client, token, payload)
    if not success:
        raise InternalException("Failed to logout")
    return {"message": "Successfully logged out"}


@router.get("/google/login")
@require_feature("ENABLE_OAUTH_LOGIN", disabled_message="OAuth login is disabled")
async def google_login(request: Request):
    if not settings.GOOGLE_CLIENT_ID:
        raise NotImplementedException("Google OAuth not configured")
    redirect_uri = settings.GOOGLE_REDIRECT_URI or request.url_for('google_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", response_model=OAuthCallbackResponse)
async def google_callback(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.GOOGLE_CLIENT_ID:
        raise NotImplementedException("Google OAuth not configured")
    
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get('userinfo')
    if not user_info:
        raise BadRequestException("Failed to get user info from Google")
    
    email = user_info.get('email')
    oauth_id = user_info.get('sub')
    
    user = await UserService.get_by_oauth(db, AuthProvider.GOOGLE, oauth_id)
    if not user:
        existing_user = await UserService.get_by_email(db, email)
        if existing_user:
            raise BadRequestException(f"Email already registered with {existing_user.auth_provider.value}")
        user = await UserService.create_oauth_user(
            db=db, email=email, provider=AuthProvider.GOOGLE, oauth_id=oauth_id,
            full_name=user_info.get('name'), avatar_url=user_info.get('picture')
        )
    else:
        await UserService.update_last_login(db, user.id)
    
    tokens = create_token_pair(user_id=user.id, email=user.email, is_admin=user.is_admin)
    return {**tokens, "user": {"id": user.id, "email": user.email, "username": user.username, "full_name": user.full_name, "avatar_url": user.avatar_url}}


@router.get("/github/login")
@require_feature("ENABLE_OAUTH_LOGIN", disabled_message="OAuth login is disabled")
async def github_login(request: Request):
    if not settings.GITHUB_CLIENT_ID:
        raise NotImplementedException("GitHub OAuth not configured")
    redirect_uri = settings.GITHUB_REDIRECT_URI or request.url_for('github_callback')
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/github/callback", response_model=OAuthCallbackResponse)
async def github_callback(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.GITHUB_CLIENT_ID:
        raise NotImplementedException("GitHub OAuth not configured")
    
    token = await oauth.github.authorize_access_token(request)
    resp = await oauth.github.get('user', token=token)
    user_info = resp.json()
    
    email_resp = await oauth.github.get('user/emails', token=token)
    emails = email_resp.json()
    primary_email = next((e['email'] for e in emails if e['primary']), emails[0]['email'])
    
    oauth_id = str(user_info.get('id'))
    github_username = user_info.get('login')
    
    user = await UserService.get_by_oauth(db, AuthProvider.GITHUB, oauth_id)
    if not user:
        existing_user = await UserService.get_by_email(db, primary_email)
        if existing_user:
            raise BadRequestException(f"Email already registered with {existing_user.auth_provider.value}")
        user = await UserService.create_oauth_user(
            db=db, email=primary_email, provider=AuthProvider.GITHUB, oauth_id=oauth_id,
            full_name=user_info.get('name'), avatar_url=user_info.get('avatar_url'),
            oauth_username=github_username
        )
    else:
        await UserService.update_last_login(db, user.id)
    
    tokens = create_token_pair(user_id=user.id, email=user.email, is_admin=user.is_admin)
    return {**tokens, "user": {"id": user.id, "email": user.email, "username": user.username, "full_name": user.full_name, "avatar_url": user.avatar_url}}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user), redis: aioredis.Redis = Depends(get_redis_client),db: AsyncSession = Depends(get_db),):
    credit_info = await CreditService.get_info(db, current_user.id)

    return UserResponse(
        email=current_user.email,
        username=current_user.username,
        is_admin=current_user.is_admin if current_user.is_admin else None ,
        created_at=current_user.created_at,
        remaining_messages=credit_info["remaining_messages"],
        total_purchased=credit_info["total_purchased"],
        total_used=credit_info["total_used"],
    )

@router.patch("/me", response_model=ProfileUpdateResponse)
async def update_profile(request: ProfileUpdateRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.update_profile(db=db, user_id=current_user.id, username=request.username, full_name=request.full_name, avatar_url=request.avatar_url)
    return {"message": "Profile updated successfully", "user": user}

@router.put("/me/password", response_model=PasswordChangeResponse)
async def change_password(request: PasswordChangeRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.change_password(db=db, user_id=current_user.id, current_password=request.current_password, new_password=request.new_password)
    return {"message": "Password changed successfully"}


@router.put("/me/email", response_model=EmailChangeResponse)
async def change_email(request: EmailChangeRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.change_email(db=db, user_id=current_user.id, new_email=request.new_email, password=request.password)
    return {"message": "Email changed successfully.", "new_email": user.email, "is_verified": user.is_verified}

