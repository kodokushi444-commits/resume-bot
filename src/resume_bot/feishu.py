from __future__ import annotations

import json

import requests


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    def _tenant_access_token(self) -> str:
        if not self.app_id or not self.app_secret:
            raise RuntimeError("未配置飞书应用凭证，请检查 .env 或 openclaw.json 里的飞书配置")
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            headers={"content-type": "application/json"},
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("tenant_access_token")
        if not token:
            raise RuntimeError(f"飞书 token 获取失败: {payload}")
        return token

    def send_text(self, receive_id: str, receive_id_type: str, text: str) -> dict:
        token = self._tenant_access_token()
        response = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
            json={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def send_interactive(self, receive_id: str, receive_id_type: str, card: dict) -> dict:
        token = self._tenant_access_token()
        response = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
            json={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
