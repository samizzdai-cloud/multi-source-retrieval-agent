"""
Google Workspace API 共用认证模块。

复用项目已有的 credentials.json / token.json，支持 Drive 和 Gmail 的 OAuth2 认证。
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


DEFAULT_CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")
DEFAULT_TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


def _env_path(name: str) -> str:
    return (os.getenv(name, "") or "").strip()


def get_credentials(
    scopes: list[str],
    token_path: str = None,
    cred_path: str = None,
) -> Credentials:
    """
    获取 Google API 认证凭据。

    逻辑：
    1. 尝试从 token.json 加载已有凭据
    2. 如果过期且有 refresh_token，自动刷新
    3. 否则启动本地 OAuth2 流授权，并保存 token.json

    Args:
        scopes: 需要的权限范围列表
        token_path: token.json 路径，默认同级目录
        cred_path: credentials.json 路径，默认同级目录

    Returns:
        google.oauth2.credentials.Credentials 对象
    """
    token_path = token_path or _env_path("GOOGLE_TOKEN_PATH") or DEFAULT_TOKEN_PATH
    cred_path = cred_path or _env_path("GOOGLE_CREDENTIALS_PATH") or DEFAULT_CREDENTIALS_PATH

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(cred_path):
                raise FileNotFoundError(
                    f"credentials.json 未找到: {cred_path}\n"
                    "请从 Google Cloud Console 下载 OAuth2 客户端凭据并重命名。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(cred_path, scopes)
            creds = flow.run_local_server(port=0)

        # 保存凭据供下次使用
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    return creds
