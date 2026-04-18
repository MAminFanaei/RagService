from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
import re

from app.middleware.exceptions import BadRequestException

class OTPPurpose(str, Enum):
    register = "register"
    reset_password = "reset_password"
    change_phone = "change_phone"
    
PHONE_REGEX = re.compile(r"^(09\d{9}|\+989\d{9}|989\d{9})$")


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    email: str
    is_admin: bool = False
    exp: Optional[int] = None
    type: str = "access"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class OAuthCallbackResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class OTPRequestBody(BaseModel):
    phone_number: str
    purpose: OTPPurpose

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v):
        phone = str(v).strip()
        if not PHONE_REGEX.match(phone):
            raise BadRequestException(
                "Phone number format is invalid" ,
                context={
                    "received_phone": phone,
                    "expected_format": "09xxxxxxxxx or +989xxxxxxxxx or 989xxxxxxxxx"
                })
        if phone.startswith("+98"):
            return "0" + phone[3:]
        if phone.startswith("98"):
            return "0" + phone[2:]
        return phone


class OTPVerifyBody(BaseModel):
    phone_number: str
    purpose: OTPPurpose
    code: str = Field(..., min_length=4, max_length=8)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v):
        phone = str(v).strip()
        if not PHONE_REGEX.match(phone):
            raise BadRequestException(
                "Phone number format is invalid" ,
                context={
                    "received_phone": phone,
                    "expected_format": "09xxxxxxxxx or +989xxxxxxxxx or 989xxxxxxxxx"
                })
        if phone.startswith("+98"):
            return "0" + phone[3:]
        if phone.startswith("98"):
            return "0" + phone[2:]
        return phone

    @field_validator("code")
    @classmethod
    def validate_code(cls, v):
        code = v.strip()
        if not code.isdigit():
            raise BadRequestException("OTP code must be numeric")
        return code


class OTPRequestResponse(BaseModel):
    message: str
    expires_in_seconds: int
    resend_after_seconds: int


class OTPVerifyResponse(BaseModel):
    message: str
    otp_proof: str
    proof_expires_in_seconds: int


class PasswordResetWithOTPRequest(BaseModel):
    phone_number: str
    new_password: str = Field(..., min_length=6, max_length=128)
    otp_proof: str

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v):
        phone = str(v).strip()
        if not PHONE_REGEX.match(phone):
            raise BadRequestException(
                "Phone number format is invalid" ,
                context={
                    "received_phone": phone,
                    "expected_format": "09xxxxxxxxx or +989xxxxxxxxx or 989xxxxxxxxx"
                })
        if phone.startswith("+98"):
            return "0" + phone[3:]
        if phone.startswith("98"):
            return "0" + phone[2:]
        return phone

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v):
        if not v.isascii():
            raise BadRequestException("Password must contain only allowed characters")
        if not re.search(r"[A-Z]", v):
            raise BadRequestException("Password must contain at least one uppercase letter")
        if not re.search(r"[0-9]", v):
            raise BadRequestException("Password must contain at least one digit")
        if len(v) < 6:
            raise BadRequestException('Password must be at least 6 characters')
        return v
    
class PhoneChangeRequest(BaseModel):
    new_phone_number: str
    otp_proof: str
    @field_validator("new_phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v):
        phone = str(v).strip()
        if not PHONE_REGEX.match(phone):
            raise BadRequestException(
                "Phone number format is invalid" ,
                context={
                    "received_phone": phone,
                    "expected_format": "09xxxxxxxxx or +989xxxxxxxxx or 989xxxxxxxxx"
                })
        if phone.startswith("+98"):
            return "0" + phone[3:]
        if phone.startswith("98"):
            return "0" + phone[2:]
        return phone


class PhoneChangeResponse(BaseModel):
    message: str
    new_phone_number: str