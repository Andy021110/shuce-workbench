from __future__ import annotations

import secrets
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    username: str
    display_name: str
    role: str

    def public(self) -> dict[str, str]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
        }


class AuthService:
    """进程内账号认证；令牌在服务重启后失效。"""

    ACCOUNTS = {
        "analyst": {
            "password": "",
            "user": AuthUser("demo_olist_analyst", "analyst", "经营分析体验", "olist_analyst"),
        },
        "admin": {
            "password": "admin123",
            "user": AuthUser("demo_admin", "admin", "系统管理员", "admin"),
        },
        "growth": {
            "password": "growth123",
            "user": AuthUser("demo_growth_ops", "growth", "用户增长运营", "growth_ops"),
        },
        "channel": {
            "password": "channel123",
            "user": AuthUser("demo_channel_ops", "channel", "渠道投放运营", "channel_ops"),
        },
        "content": {
            "password": "content123",
            "user": AuthUser("demo_content_ops", "content", "内容运营", "content_ops"),
        },
    }

    def __init__(self) -> None:
        self._tokens: dict[str, AuthUser] = {}

    def login(self, username: str, password: str) -> tuple[str, AuthUser] | None:
        username = username.strip()
        account = self.ACCOUNTS.get(username)
        if not account:
            return None
        configured = os.getenv(f"SHUCE_PASSWORD_{username.upper()}")
        public = os.getenv("SHUCE_PUBLIC_DEPLOYMENT", "").lower() in {"1", "true", "yes"}
        if public and (not configured or len(configured) < 16 or configured == account["password"]):
            return None
        expected = configured if configured is not None else str(account["password"])
        if not expected or not secrets.compare_digest(expected, password):
            return None
        token = secrets.token_urlsafe(32)
        user = account["user"]
        self._tokens[token] = user
        return token, user

    def authenticate(self, authorization: str | None) -> AuthUser | None:
        if not authorization:
            return None
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return self._tokens.get(token)

    def logout(self, authorization: str | None) -> None:
        if not authorization:
            return
        _, _, token = authorization.partition(" ")
        self._tokens.pop(token, None)
