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
from app.services.user_service import UserService
from app.services.rate_limit_service import RateLimitService
from app.models.user import AuthProvider, User
from app.api.deps import get_current_user, get_redis_client
from app.config import settings
from app.core.feature_flags import require_feature
from app.middleware.exceptions import BadRequestException, InternalException, NotImplementedException, UnauthorizedException
from app.services.otp_service import OtpService
from app.schemas.user import (
    AccountDeleteRequest, UserCreate, UserLogin, UserResponse,
    PasswordChangeRequest, EmailChangeRequest, ProfileUpdateRequest,
    PasswordChangeResponse, EmailChangeResponse, ProfileUpdateResponse , SuccessResponse
)
from app.schemas.auth import (
    OTPPurpose,
    PhoneChangeRequest,
    PhoneChangeResponse,
    Token,
    RefreshTokenRequest,
    OAuthCallbackResponse,
    OTPRequestBody,
    OTPVerifyBody,
    OTPRequestResponse,
    OTPVerifyResponse,
    PasswordResetWithOTPRequest,
)


security = HTTPBearer()
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
@require_feature(flag_name="ENABLE_REGISTRATION", disabled_message="Registration is disabled")
async def register(
    request: Request,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    # ── Step 1: validate proof is structurally valid (does NOT consume it yet) ──
    from app.services.otp_service import OtpService
    await OtpService.validate_proof_without_consuming(
        redis=redis,
        proof_token=user_data.otp_proof,
        expected_phone=user_data.phone_number,
        expected_purpose="register",
    )

    # ── Step 2: validate all business rules BEFORE consuming proof ──
    existing_phone = await UserService.get_by_phone(db, user_data.phone_number)
    if existing_phone:
        raise BadRequestException("Phone number already registered")

    if user_data.email:
        existing_user = await UserService.get_by_email(db, user_data.email)
        if existing_user:
            raise BadRequestException("Email already registered")

    if user_data.username:
        existing_username = await UserService.get_by_username(db, user_data.username)
        if existing_username:
            raise BadRequestException("Username already taken")

    # ── Step 3: everything is valid, NOW consume proof (point of no return) ──
    await OtpService.consume_verification_proof(
        redis=redis,
        proof_token=user_data.otp_proof,
        expected_phone=user_data.phone_number,
        expected_purpose="register",
    )

    # ── Step 4: create user ──
    user = await UserService.create_user(db, user_data)
    return create_token_pair(
        user_id=user.id,
        email=user.email,
        username=user.username,
        is_admin=user.is_admin
    )

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

@router.post("/reset_password", response_model=SuccessResponse)
async def reset_password_with_otp(
    payload: PasswordResetWithOTPRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    await OtpService.consume_verification_proof(
        redis=redis,
        proof_token=payload.otp_proof,
        expected_phone=payload.phone_number,
        expected_purpose="reset_password",
    )
    await UserService.reset_password_by_phone(
        db=db,
        phone_number=payload.phone_number,
        new_password=payload.new_password
    )
    return {"message": "Password reset successfully"}


# @router.get("/google/login")
# @require_feature("ENABLE_OAUTH_LOGIN", disabled_message="OAuth login is disabled")
# async def google_login(request: Request):
#     if not settings.GOOGLE_CLIENT_ID:
#         raise NotImplementedException("Google OAuth not configured")
#     redirect_uri = settings.GOOGLE_REDIRECT_URI or request.url_for('google_callback')
#     return await oauth.google.authorize_redirect(request, redirect_uri)


# @router.get("/google/callback", response_model=OAuthCallbackResponse)
# async def google_callback(request: Request, db: AsyncSession = Depends(get_db)):
#     if not settings.GOOGLE_CLIENT_ID:
#         raise NotImplementedException("Google OAuth not configured")
    
#     token = await oauth.google.authorize_access_token(request)
#     user_info = token.get('userinfo')
#     if not user_info:
#         raise BadRequestException("Failed to get user info from Google")
    
#     email = user_info.get('email')
#     oauth_id = user_info.get('sub')
    
#     user = await UserService.get_by_oauth(db, AuthProvider.GOOGLE, oauth_id)
#     if not user:
#         existing_user = await UserService.get_by_email(db, email)
#         if existing_user:
#             raise BadRequestException(f"Email already registered with {existing_user.auth_provider.value}")
#         user = await UserService.create_oauth_user(
#             db=db, email=email, provider=AuthProvider.GOOGLE, oauth_id=oauth_id,
#             full_name=user_info.get('name'), avatar_url=user_info.get('picture')
#         )
#     else:
#         await UserService.update_last_login(db, user.id)
    
#     tokens = create_token_pair(user_id=user.id, email=user.email, is_admin=user.is_admin)
#     return {**tokens, "user": {"id": user.id, "email": user.email, "username": user.username, "full_name": user.full_name, "avatar_url": user.avatar_url}}

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db),
):
    credit_info = await CreditService.get_info(db, current_user.id)
    await db.commit()
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        auth_provider=current_user.auth_provider,
        is_active=current_user.is_active,
        is_admin=current_user.is_admin,       # ← always pass the real value, never mask it
        is_verified=current_user.is_verified,
        avatar_url=current_user.avatar_url,
        created_at=current_user.created_at,
        last_login_at=current_user.last_login_at,
        remaining_messages=credit_info["remaining_messages"],
        total_purchased=credit_info["total_purchased"],
        total_used=credit_info["total_used"],
    )

@router.patch("/me", response_model=ProfileUpdateResponse)
async def update_profile(request: ProfileUpdateRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.update_profile(db=db, user_id=current_user.id, username=request.username, full_name=request.full_name, avatar_url=request.avatar_url)
    return {"message": "Profile updated successfully", "user": user}

@router.put("/me/reset-password", response_model=PasswordChangeResponse)
async def change_password(request: PasswordChangeRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.change_password(db=db, user_id=current_user.id, current_password=request.current_password, new_password=request.new_password)
    return {"message": "Password changed successfully"}


@router.put("/me/change_email", response_model=EmailChangeResponse)
async def change_email(request: EmailChangeRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.change_email(db=db, user_id=current_user.id, new_email=request.new_email, password=request.password)
    return {"message": "Email changed successfully.", "new_email": user.email, "is_verified": user.is_verified}

@router.put("/me/change_phone", response_model=PhoneChangeResponse)
async def change_phone(
    request: PhoneChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    # Step 1: validate proof without consuming
    await OtpService.validate_proof_without_consuming(
        redis=redis,
        proof_token=request.otp_proof,
        expected_phone=request.new_phone_number,
        expected_purpose=OTPPurpose.change_phone.value,
    )

    # Step 2: check phone is not already taken
    existing = await UserService.get_by_phone(db, request.new_phone_number)
    if existing and existing.id != current_user.id:
        raise BadRequestException("Phone number is already in use")

    # Step 3: all good, consume proof
    await OtpService.consume_verification_proof(
        redis=redis,
        proof_token=request.otp_proof,
        expected_phone=request.new_phone_number,
        expected_purpose=OTPPurpose.change_phone.value,
    )

    # Step 4: update phone
    user = await UserService.change_phone(
        db=db,
        user_id=current_user.id,
        new_phone_number=request.new_phone_number,
    )
    return {
        "message": "Phone number changed successfully",
        "new_phone_number": user.phone_number,
    }

@router.post("/otp/request", response_model=OTPRequestResponse)
async def request_otp(
    payload: OTPRequestBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    phone = OtpService.normalize_phone(payload.phone_number)

    if payload.purpose == OTPPurpose.register:
        existing_user = await UserService.get_by_phone(db, phone)
        if existing_user:
            raise BadRequestException("Phone number is already registered")
    
    elif payload.purpose == OTPPurpose.change_phone:
        existing_user = await UserService.get_by_phone(db, phone)
        if existing_user:
            raise BadRequestException("Phone number is already in use")
        
    elif payload.purpose == OTPPurpose.reset_password:
        existing_user = await UserService.get_by_phone(db, phone)
        if not existing_user:
            raise BadRequestException("No user found by this number")

    await OtpService.request_otp(redis=redis, phone_number=phone, purpose=payload.purpose.value)
    return {
        "message": "OTP sent successfully",
        "expires_in_seconds": settings.OTP_EXPIRE_SECONDS,
        "resend_after_seconds": settings.OTP_RESEND_COOLDOWN_SECONDS,
    }


@router.post("/otp/verify", response_model=OTPVerifyResponse)
async def verify_otp(payload: OTPVerifyBody, redis: aioredis.Redis = Depends(get_redis)):
    phone = OtpService.normalize_phone(payload.phone_number)
    otp_proof = await OtpService.verify_otp_and_issue_proof(
        redis=redis,
        phone_number=phone,
        purpose=payload.purpose,
        code=payload.code,
    )
    return {
        "message": "OTP verified successfully",
        "otp_proof": otp_proof,
        "proof_expires_in_seconds": settings.OTP_VERIFY_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.delete("/me", status_code=status.HTTP_200_OK)
async def delete_account(
    payload: AccountDeleteRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """
    Soft-delete current user's account.
    
    Requirements:
        - Correct password
        - confirm_deletion=true
        - No wallet balance OR forfeit_balance=true
        - No pending payments <1 hour old
    
    Actions:
        - Marks account inactive (is_active=False)
        - Anonymizes email/phone/username
        - Cancels old pending payments (>1h)
        - Keeps payment/wallet records (compliance)
        - Blacklists current token (logout)
    
    Note: Chat history will be deleted in 30 days (manual cleanup).
    """
    # Perform soft delete with all validation checks
    result = await UserService.soft_delete_account(
        db=db,
        user_id=current_user.id,
        password=payload.password,
        confirm_deletion=payload.confirm_deletion,
        forfeit_balance=payload.forfeit_balance
    )
    
    # Blacklist current token (same pattern as logout endpoint)
    token = credentials.credentials
    token_payload = decode_token(token)
    if token_payload:
        await blacklist_token(redis, token, token_payload)
    
    return {
        "message": "Account successfully deleted",
        "details": result
    }