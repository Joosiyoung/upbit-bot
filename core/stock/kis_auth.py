import json
import os
import requests
from datetime import datetime, timedelta

from core import config

_KST = config.KST


class KisAuth:
    BASE_URL_REAL    = "https://openapi.koreainvestment.com:9443"
    BASE_URL_SANDBOX = "https://openapivts.koreainvestment.com:29443"
    TOKEN_CACHE_FILE = "data/kis_token_cache.json"

    def __init__(self, app_key: str, app_secret: str, is_sandbox: bool = True):
        self._app_key    = app_key
        self._app_secret = app_secret
        self._is_sandbox = is_sandbox
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._load_cache()

    @property
    def base_url(self) -> str:
        return self.BASE_URL_SANDBOX if self._is_sandbox else self.BASE_URL_REAL

    def get_token(self) -> str:
        now = datetime.now(_KST)
        # 만료 30분 전이면 갱신
        if self._token and self._expires_at and self._expires_at - now > timedelta(minutes=30):
            return self._token
        return self._fetch_token()

    def get_hashkey(self, body: dict) -> str:
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "appkey":    self._app_key,
            "appsecret": self._app_secret,
            "Content-Type": "application/json",
        }
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json().get("HASH", "")

    def _fetch_token(self) -> str:
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey":     self._app_key,
            "appsecret":  self._app_secret,
        }
        try:
            resp = requests.post(url, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            token      = data["access_token"]
            expires_in = int(data.get("expires_in", 86400))
            expires_at = datetime.now(_KST) + timedelta(seconds=expires_in)
            self._token      = token
            self._expires_at = expires_at
            self._save_cache()
            return token
        except Exception as e:
            if self._token:
                # 갱신 실패 시 기존 토큰 유지
                return self._token
            raise RuntimeError(f"KIS 토큰 발급 실패: {e}") from e

    def _load_cache(self):
        try:
            with open(self.TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._token      = data.get("access_token")
            expires_str      = data.get("expires_at")
            if expires_str:
                self._expires_at = datetime.fromisoformat(expires_str)
                # naive datetime이면 KST-aware로 변환
                if self._expires_at.tzinfo is None:
                    self._expires_at = self._expires_at.replace(tzinfo=_KST)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    def _save_cache(self):
        os.makedirs(os.path.dirname(self.TOKEN_CACHE_FILE), exist_ok=True)
        data = {
            "access_token": self._token,
            "expires_at":   self._expires_at.isoformat() if self._expires_at else None,
        }
        with open(self.TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
