import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
DISPLAY_NAME_MAX_LENGTH = 120


class WeakPasswordError(ValueError):
    pass


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 255 or not EMAIL_PATTERN.match(email):
        raise ValueError("Email không hợp lệ.")
    return email


def check_password(password: str, email: str | None = None) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise WeakPasswordError(f"Mật khẩu phải có ít nhất {PASSWORD_MIN_LENGTH} ký tự.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise WeakPasswordError(f"Mật khẩu tối đa {PASSWORD_MAX_LENGTH} ký tự.")
    if not re.search(r"[^\W\d_]", password) or not re.search(r"\d", password):
        raise WeakPasswordError("Mật khẩu phải có cả chữ lẫn số.")
    if email and password.strip().lower() == email.strip().lower():
        raise WeakPasswordError("Mật khẩu không được trùng email.")
    return password


def clean_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    name = " ".join(value.split())
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(f"Tên hiển thị tối đa {DISPLAY_NAME_MAX_LENGTH} ký tự.")
    return name or None


class RegisterIn(BaseModel):
    email: str
    password: str
    display_name: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str | None) -> str | None:
        return clean_display_name(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    last_login_at: datetime | None
    created_at: datetime

    @property
    def label(self) -> str:
        return self.display_name or self.email
