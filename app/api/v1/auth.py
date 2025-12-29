from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from app.core.database import get_db, get_redis
from app.core.security import create_token_pair, decode_token, oauth
from app.schemas.user import UserCreate, UserLogin, UserResponse
from app.schemas.auth import Token, RefreshTokenRequest, OAuthCallbackResponse
from app.services.user_service import UserService
from app.models.user import AuthProvider, User
from app.api.deps import get_current_user
from app.config import settings
import redis.asyncio as aioredis
from fastapi.security import HTTPBearer , HTTPAuthorizationCredentials

security = HTTPBearer()

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """Register a new user with email/username and password"""
    # Check if email exists
    existing_user = UserService.get_by_email(db, user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Check if username exists (if provided)
    if user_data.username:
        existing_username = UserService.get_by_username(db, user_data.username)
        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken"
            )
    
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email/username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    user_id = payload.get("sub")
    user = UserService.get_by_id(db, user_id)
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    
    # Generate new tokens
    tokens = create_token_pair(
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin
    )
    
    return tokens


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """Get current user information"""
    return current_user


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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    
    # Blacklist the access token
    success = await blacklist_token(redis_client, token, payload)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to logout",
        )
    
    return {"message": "Successfully logged out"}


# ==================== GOOGLE OAUTH ====================

@router.get("/google/login")
async def google_login(request: Request):
    """Initiate Google OAuth flow"""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth not configured"
        )
    
    redirect_uri = settings.GOOGLE_REDIRECT_URI or request.url_for('google_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", response_model=OAuthCallbackResponse)
async def google_callback(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle Google OAuth callback"""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth not configured"
        )
    
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to get user info from Google"
            )
        
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
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Email already registered with {existing_user.auth_provider.value}"
                )
            
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth authentication failed: {str(e)}"
        )


# ==================== GITHUB OAUTH ====================

@router.get("/github/login")
async def github_login(request: Request):
    """Initiate GitHub OAuth flow"""
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="GitHub OAuth not configured"
        )
    
    redirect_uri = settings.GITHUB_REDIRECT_URI or request.url_for('github_callback')
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/github/callback", response_model=OAuthCallbackResponse)
async def github_callback(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle GitHub OAuth callback"""
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="GitHub OAuth not configured"
        )
    
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
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Email already registered with {existing_user.auth_provider.value}"
                )
            
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth authentication failed: {str(e)}"
        )