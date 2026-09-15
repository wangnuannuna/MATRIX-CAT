import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import get_settings

settings = get_settings()
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")  # 国内手机号


def _check_phone(v: str) -> str:
    if not PHONE_RE.match(v):
        raise ValueError("手机号格式不对")
    return v


class SmsSendReq(BaseModel):
    phone: str

    _p = field_validator("phone")(_check_phone)


class RegisterReq(BaseModel):
    phone: str
    code: str
    password: str
    username: str | None = None

    _p = field_validator("phone")(_check_phone)

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        if len(v) < settings.pwd_min_len:
            raise ValueError(f"密码至少 {settings.pwd_min_len} 位")
        return v

    @field_validator("username")
    @classmethod
    def _un(cls, v):
        if v is None:
            return v
        if not (2 <= len(v) <= 64):
            raise ValueError("用户名 2~64 位")
        if v.isdigit():
            raise ValueError("用户名不能是纯数字(避免和手机号混淆)")
        return v


class PasswordLoginReq(BaseModel):
    account: str  # 手机号或用户名
    password: str


class SmsLoginReq(BaseModel):
    phone: str
    code: str

    _p = field_validator("phone")(_check_phone)


class RefreshReq(BaseModel):
    refresh_token: str


class LogoutReq(BaseModel):
    refresh_token: str


class TokenResp(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access 剩余秒数


class MembershipResp(BaseModel):
    plan_code: str | None = None
    plan_name: str | None = None
    max_accounts: int = 0
    used_accounts: int = 0
    active: bool = False
    expires_at: datetime | None = None


class UserResp(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    phone: str
    username: str | None = None
    status: str


class MeResp(BaseModel):
    user: UserResp
    membership: MembershipResp


class BindAccountReq(BaseModel):
    platform: str = Field(max_length=32)
    account_name: str = Field(min_length=1, max_length=128)


class SocialAccountResp(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    platform: str
    account_name: str
    status: str
    created_at: datetime
