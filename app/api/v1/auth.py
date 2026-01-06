from fastapi import APIRouter, Depends, status, Request
from sqlalchemy.orm import Session

from app.core.database import get_db, get_redis
from app.core.security import create_token_pair, decode_token, oauth
from app.schemas.user import UserCreate, UserLogin, UserResponse
from app.schemas.auth import Token, RefreshTokenRequest, OAuthCallbackResponse
from app.services.user_service import UserService
from app.models.user import AuthProvider, User
from app.api.deps import get_current_user , get_redis_client
from app.config import settings
from app.core.feature_flags import require_feature
from app.services.rate_limit_service import RateLimitService
import redis.asyncio as aioredis
from fastapi.security import HTTPBearer , HTTPAuthorizationCredentials
from app.exceptions import BadRequestException, InternalException, NotImplementedException, UnauthorizedException

security = HTTPBearer()

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
@require_feature(flag_name="ENABLE_REGISTRATION",disabled_message="Regesteration is disabled")  # ← Add this line
async def register(
    request: Request,
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """Register a new user with email/username and password"""
    # Check if email exists
    existing_user = UserService.get_by_email(db, user_data.email)
    if existing_user:
        raise BadRequestException("Email already registered")

    
    # Check if username exists (if provided)
    if user_data.username:
        existing_username = UserService.get_by_username(db, user_data.username)
        if existing_username:
            raise BadRequestException("Username already taken")
    
    # Create user
    user = UserService.create_user(db, user_data)
    
    # Generate tokens
    tokens = create_token_pair(
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin
    )
    
    return tokens


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    credentials: UserLogin,
    db: Session = Depends(get_db)
):
    """Login with email/username and password"""
    user = UserService.authenticate(db, credentials.login, credentials.password)
    
    if not user:
        raise UnauthorizedException("Incorrect email/username or password")
    
    # Generate tokens
    tokens = create_token_pair(
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin
    )
    
    return tokens


@router.post("/refresh", response_model=Token)
async def refresh_token(
    token_request: RefreshTokenRequest,
    db: Session = Depends(get_db)
):
    """Refresh access token using refresh token"""
    payload = decode_token(token_request.refresh_token)
    
    if not payload or payload.get("type") != "refresh":
        raise UnauthorizedException("Invalid refresh token")
    
    user_id = payload.get("sub")
    user = UserService.get_by_id(db, user_id)
    
    if not user or not user.is_active:
        raise UnauthorizedException("User not found or inactive")

    
    # Generate new tokens
    tokens = create_token_pair(
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin
    )
    
    return tokens

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis_client)
):
    """Get current user information"""
    
    rate_per_minute , quota_per_day = RateLimitService.get_user_limits(current_user)
    _ , remaining = await RateLimitService.check_daily_quota(
        redis, current_user.id, quota_per_day
    )
    
    return UserResponse(
        email=current_user.email,
        username=current_user.username,
        is_active=current_user.is_active,
        is_admin=current_user.is_admin,
        created_at=current_user.created_at,
        remaining_messages_today=remaining  
    )

# In auth.py, update the logout endpoint:

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    redis_client: aioredis.Redis = Depends(get_redis)
):
    """
    Logout user by blacklisting their current access token.
    
    The token will be blacklisted until it expires.
    Client should also discard the refresh token.
    """
    from app.core.security import decode_token, blacklist_token
    
    token = credentials.credentials
    payload = decode_token(token)
    
    if not payload:
        raise UnauthorizedException("Invalid token")
    
    # Blacklist the access token
    success = await blacklist_token(redis_client, token, payload)
    
    if not success:
        raise InternalException("Failed to logout")
    
    return {"message": "Successfully logged out"}


# ==================== GOOGLE OAUTH ====================

@router.get("/google/login")
@require_feature("ENABLE_OAUTH_LOGIN", disabled_message="OAuth login is disabled")
async def google_login(request: Request):
    """Initiate Google OAuth flow"""
    if not settings.GOOGLE_CLIENT_ID:
        raise NotImplementedException("Google OAuth not configured")
    
    redirect_uri = settings.GOOGLE_REDIRECT_URI or request.url_for('google_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", response_model=OAuthCallbackResponse)
@require_feature("ENABLE_OAUTH_LOGIN", disabled_message="OAuth login is disabled")
async def google_callback(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle Google OAuth callback"""
    if not settings.GOOGLE_CLIENT_ID:
        raise NotImplementedException("Google OAuth not configured")
    
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        
        if not user_info:
            raise BadRequestException("Failed to get user info from Google")
        
        email = user_info.get('email')
        oauth_id = user_info.get('sub')
        full_name = user_info.get('name')
        avatar_url = user_info.get('picture')
        
        # Check if user exists
        user = UserService.get_by_oauth(db, AuthProvider.GOOGLE, oauth_id)
        
        if not user:
            # Check if email already exists with different provider
            existing_user = UserService.get_by_email(db, email)
            if existing_user:
                raise BadRequestException(f"Email already registered ")
            
            # Create new user
            user = UserService.create_oauth_user(
                db=db,
                email=email,
                provider=AuthProvider.GOOGLE,
                oauth_id=oauth_id,
                full_name=full_name,
                avatar_url=avatar_url
            )
        else:
            # Update last login
            UserService.update_last_login(db, user.id)
        
        # Generate tokens
        tokens = create_token_pair(
            user_id=user.id,
            email=user.email,
            is_admin=user.is_admin
        )
        
        return {
            **tokens,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "avatar_url": user.avatar_url
            }
        }
    
    except Exception as e:
        raise BadRequestException("OAuth authentication failed")


# ==================== GITHUB OAUTH ====================

@router.get("/github/login")
@require_feature("ENABLE_OAUTH_LOGIN", disabled_message="OAuth login is disabled")
async def github_login(request: Request):
    """Initiate GitHub OAuth flow"""
    if not settings.GITHUB_CLIENT_ID:
        raise NotImplementedException("GitHub OAuth not configured")
    
    redirect_uri = settings.GITHUB_REDIRECT_URI or request.url_for('github_callback')
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/github/callback", response_model=OAuthCallbackResponse)
@require_feature("ENABLE_OAUTH_LOGIN", disabled_message="OAuth login is disabled")
async def github_callback(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle GitHub OAuth callback"""
    if not settings.GITHUB_CLIENT_ID:
        raise NotImplementedException("GitHub OAuth not configured")
    
    try:
        token = await oauth.github.authorize_access_token(request)
        
        # Get user info
        resp = await oauth.github.get('user', token=token)
        user_info = resp.json()
        
        # Get primary email
        email_resp = await oauth.github.get('user/emails', token=token)
        emails = email_resp.json()
        primary_email = next((e['email'] for e in emails if e['primary']), emails[0]['email'])
        
        oauth_id = str(user_info.get('id'))
        full_name = user_info.get('name')
        avatar_url = user_info.get('avatar_url')
        
        # Check if user exists
        user = UserService.get_by_oauth(db, AuthProvider.GITHUB, oauth_id)
        
        if not user:
            # Check if email already exists with different provider
            existing_user = UserService.get_by_email(db, primary_email)
            if existing_user:
                raise BadRequestException(f"Email already registered")
            
            # Create new user
            user = UserService.create_oauth_user(
                db=db,
                email=primary_email,
                provider=AuthProvider.GITHUB,
                oauth_id=oauth_id,
                full_name=full_name,
                avatar_url=avatar_url
            )
        else:
            # Update last login
            UserService.update_last_login(db, user.id)
        
        # Generate tokens
        tokens = create_token_pair(
            user_id=user.id,
            email=user.email,
            is_admin=user.is_admin
        )
        
        return {
            **tokens,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "avatar_url": user.avatar_url
            }
        }
    
    except Exception as e:
        raise BadRequestException("OAuth authentication failed")