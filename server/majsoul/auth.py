"""CN Mahjong Soul password and OAuth token authentication behavior."""
from __future__ import annotations

import hashlib
import hmac
import uuid

from .errors import MajsoulError, MajsoulTokenInvalid

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

class AuthMixin:
    def _device(self, random_key: str):
        return {
            "platform": "pc",
            "hardware": "unknown",
            "os": "windows",
            "os_version": "Windows 10",
            "is_browser": True,
            "sale_platform": "web",
            "screen_width": 1920,
            "screen_height": 1080,
            "user_agent": UA,
            "screen_type": 1,
            "uuid": random_key,
        }

    async def login_password(self, account: str, password: str):
        pw = hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()
        # In CN Mahjong Soul: 0 = email/account, 1 = mobile phone
        primary_type = 1 if account.strip().isdigit() else 0
        types_to_try = [primary_type, 1 - primary_type]
        last_err = None
        res = None
        for acc_type in types_to_try:
            rkey = str(uuid.uuid4())
            try:
                res = await self.call(
                    "login",
                    account=account,
                    password=pw,
                    reconnect=False,
                    device=self._device(rkey),
                    random_key=rkey,
                    client_version=self.client_version_info,
                    gen_access_token=True,
                    currency_platforms=[1, 2, 5, 6, 8, 10, 11],
                    type=acc_type,
                    client_version_string=self.client_version_string,
                    tag="cn",
                )
                break
            except MajsoulError as e:
                last_err = e
                # Only retry with alternate type on account-not-found / wrong-type errors
                if "1003" in str(e) or "104" in str(e) or "1004" in str(e):
                    continue
                raise
        if res is None:
            raise last_err or MajsoulError("登录失败")
        self._after_login(res)
        self.access_token = res.access_token or self.access_token
        self.current_account = account
        return res

    async def login_token(self, token: str):
        # Only an explicit token rejection permits a password fallback.
        try:
            chk = await self.call("oauth2Check", type=0, access_token=token)
        except MajsoulError as exc:
            if exc.code == 109:
                raise MajsoulTokenInvalid("access_token 无效或已过期", code=109) from exc
            raise
        if not getattr(chk, "has_account", True):
            raise MajsoulTokenInvalid("access_token 无效或已过期", code=109)
        rkey = str(uuid.uuid4())
        try:
            res = await self.call(
                "oauth2Login",
                type=0,
                access_token=token,
                reconnect=False,
                device=self._device(rkey),
                random_key=rkey,
                client_version=self.client_version_info,
                gen_access_token=False,
                currency_platforms=[1, 2, 5, 6, 8, 10, 11],
                client_version_string=self.client_version_string,
                tag="cn",
            )
        except MajsoulError as exc:
            if exc.code == 109:
                raise MajsoulTokenInvalid("access_token 无效或已过期", code=109) from exc
            raise
        self._after_login(res)
        self.access_token = token
        return res

    def _after_login(self, res):
        if not res.account_id:
            raise MajsoulError("登录失败（未返回账号信息）")
        self.logged_in = True
        self.account_id = res.account_id
        self.nickname = res.account.nickname if res.HasField("account") else None

