import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

from feishu_read_tool.oauth_tokens import (
    FeishuOAuthError,
    build_authorization_url,
    ensure_valid_project_access_token,
    exchange_authorization_code,
    _loopback_callback_parts,
    token_status,
)
from retrieve_agent import _resolve_feishu_uat
from tools.core.models import SourceConfig


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FeishuOAuthTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp.name) / ".env"
        self.env_path.write_text(
            "FEISHU_APP_ID=cli_test\nFEISHU_APP_SECRET=secret\n"
            "FEISHU_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/callback\n"
            "FEISHU_OAUTH_SCOPES=offline_access search:docs:read docx:document:readonly\n"
            "Feishu_UAT=old-token\nOTHER=value\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_exchange_persists_rotated_pair_atomically_and_normalizes_key(self):
        payload = {
            "code": 0, "access_token": "new-access", "expires_in": 7200,
            "refresh_token": "new-refresh", "refresh_token_expires_in": 604800,
        }
        with patch("feishu_read_tool.oauth_tokens.requests.post", return_value=_Response(payload)) as post:
            state = exchange_authorization_code("one-use-code", env_path=self.env_path)

        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["grant_type"], "authorization_code")
        self.assertEqual(sent["code"], "one-use-code")
        self.assertEqual(state.access_token, "new-access")
        values = dotenv_values(self.env_path)
        self.assertEqual(values["FEISHU_UAT"], "new-access")
        self.assertEqual(values["FEISHU_REFRESH_TOKEN"], "new-refresh")
        self.assertNotIn("Feishu_UAT", values)
        self.assertEqual(values["OTHER"], "value")

    def test_expiring_access_uses_refresh_and_replaces_one_use_refresh_token(self):
        self.env_path.write_text(
            "FEISHU_APP_ID=cli_test\nFEISHU_APP_SECRET=secret\n"
            "FEISHU_UAT=old-access\nFEISHU_UAT_EXPIRES_AT=1\n"
            "FEISHU_REFRESH_TOKEN=old-refresh\n",
            encoding="utf-8",
        )
        payload = {
            "code": 0, "access_token": "rotated-access", "expires_in": 7200,
            "refresh_token": "rotated-refresh", "refresh_token_expires_in": 604800,
        }
        with patch("feishu_read_tool.oauth_tokens.requests.post", return_value=_Response(payload)) as post:
            token = ensure_valid_project_access_token(env_path=self.env_path)

        self.assertEqual(token, "rotated-access")
        self.assertEqual(post.call_args.kwargs["json"]["refresh_token"], "old-refresh")
        values = dotenv_values(self.env_path)
        self.assertEqual(values["FEISHU_REFRESH_TOKEN"], "rotated-refresh")

    def test_authorization_url_requires_search_and_offline_scopes(self):
        url = build_authorization_url(env_path=self.env_path, state="state-value")
        self.assertIn("client_id=cli_test", url)
        self.assertIn("search%3Adocs%3Aread", url)

        self.env_path.write_text(
            "FEISHU_APP_ID=cli_test\nFEISHU_OAUTH_REDIRECT_URI=http://127.0.0.1/callback\n"
            "FEISHU_OAUTH_SCOPES=offline_access\n",
            encoding="utf-8",
        )
        with self.assertRaises(FeishuOAuthError):
            build_authorization_url(env_path=self.env_path)

    def test_temporary_authorization_overrides_do_not_require_env_write(self):
        self.env_path.write_text("FEISHU_APP_ID=cli_test\n", encoding="utf-8")
        url = build_authorization_url(
            env_path=self.env_path,
            state="state-value",
            redirect_uri="http://127.0.0.1:8765/callback",
            scopes="offline_access search:docs:read",
        )
        self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback", url)

    def test_loopback_callback_rejects_non_loopback_uri(self):
        self.assertEqual(
            _loopback_callback_parts("http://127.0.0.1:8765/callback"),
            ("127.0.0.1", 8765, "/callback"),
        )
        with self.assertRaises(FeishuOAuthError):
            _loopback_callback_parts("https://open.feishu.cn/callback")

    def test_status_never_returns_token_values(self):
        status = token_status(env_path=self.env_path)
        serialized = str(status)
        self.assertNotIn("old-token", serialized)
        self.assertNotIn(": 'secret'", serialized)
        self.assertTrue(status["app_id_present"])
        self.assertTrue(status["app_secret_present"])
        self.assertTrue(status["access_token_present"])

    def test_normal_runtime_path_uses_lifecycle_manager(self):
        with patch(
            "feishu_read_tool.oauth_tokens.ensure_valid_project_access_token",
            return_value="rotated-access",
        ) as ensure:
            self.assertEqual(_resolve_feishu_uat(SourceConfig()), "rotated-access")
        ensure.assert_called_once_with()

    def test_explicit_uat_skips_lifecycle_manager(self):
        self.assertEqual(
            _resolve_feishu_uat(SourceConfig(feishu_uat="caller-owned", load_from_env=False)),
            "caller-owned",
        )


if __name__ == "__main__":
    unittest.main()
