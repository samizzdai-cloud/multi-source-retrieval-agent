"""Feishu OAuth v2 token lifecycle for the local retrieval runtime.

This module owns only credentials stored in the project ``.env``.  It never
logs token values, and it rotates the access and refresh tokens in one atomic
write because Feishu refresh tokens are single-use.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import dotenv_values


OAUTH_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
OAUTH_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
OAUTH_DIAGNOSTIC_PATH = Path(tempfile.gettempdir()) / "multi-source-retrieval-agent-feishu-oauth.json"
ACCESS_EXPIRY_SKEW_S = 300
_LOCK = threading.Lock()
_TOKEN_ENV_KEYS = {
    "FEISHU_UAT",
    "FEISHU_REFRESH_TOKEN",
    "FEISHU_UAT_EXPIRES_AT",
    "FEISHU_REFRESH_TOKEN_EXPIRES_AT",
}


class FeishuOAuthError(RuntimeError):
    """Raised for safe, actionable OAuth lifecycle failures."""


@dataclass(frozen=True)
class TokenState:
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: int = 0
    refresh_expires_at: int = 0
    app_id: str = ""
    app_secret: str = ""
    redirect_uri: str = ""
    scopes: str = ""


def _canonical_env(values: Mapping[str, Optional[str]]) -> dict[str, str]:
    """Read env keys case-insensitively so the old ``Feishu_UAT`` still works."""
    canonical: dict[str, str] = {}
    for key, value in values.items():
        if value is not None:
            canonical[str(key).upper()] = str(value)
    return canonical


def _env_values(env_path: Path) -> dict[str, str]:
    file_values = _canonical_env(dotenv_values(env_path) if env_path.exists() else {})
    # Project .env is the source of truth for rotation.  Process variables are
    # retained only when a key is absent from the project file.
    for key, value in os.environ.items():
        file_values.setdefault(key.upper(), value)
    return file_values


def _as_int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _jwt_expiration(token: str) -> int:
    """Return unverified JWT ``exp`` only as a local expiry hint."""
    if token.count(".") != 2:
        return 0
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        return int(data.get("exp") or 0)
    except (ValueError, TypeError, UnicodeDecodeError):
        return 0


def load_token_state(env_path: Path = DEFAULT_ENV_PATH) -> TokenState:
    values = _env_values(env_path)
    access_token = values.get("FEISHU_UAT", "")
    return TokenState(
        access_token=access_token,
        refresh_token=values.get("FEISHU_REFRESH_TOKEN", ""),
        access_expires_at=_as_int(values.get("FEISHU_UAT_EXPIRES_AT", "")) or _jwt_expiration(access_token),
        refresh_expires_at=_as_int(values.get("FEISHU_REFRESH_TOKEN_EXPIRES_AT", "")),
        app_id=values.get("FEISHU_APP_ID", ""),
        app_secret=values.get("FEISHU_APP_SECRET", ""),
        redirect_uri=values.get("FEISHU_OAUTH_REDIRECT_URI", ""),
        scopes=values.get("FEISHU_OAUTH_SCOPES", ""),
    )


def _needs_refresh(state: TokenState, *, now: Optional[float] = None, skew_s: int = ACCESS_EXPIRY_SKEW_S) -> bool:
    if not state.access_token:
        return bool(state.refresh_token)
    if not state.access_expires_at:
        return False
    return (now if now is not None else time.time()) + skew_s >= state.access_expires_at


def _quote_env(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _atomic_update_env(env_path: Path, updates: Mapping[str, str]) -> None:
    """Replace credential keys atomically while preserving unrelated .env lines."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = existing.splitlines(keepends=True)
    remaining = {key.upper(): str(value) for key, value in updates.items()}
    rendered: list[str] = []
    seen: set[str] = set()

    for line in lines:
        raw = line.rstrip("\r\n")
        if "=" not in raw or raw.lstrip().startswith("#"):
            rendered.append(line)
            continue
        lhs = raw.split("=", 1)[0].strip()
        normalized = lhs.removeprefix("export ").strip().upper()
        if normalized not in remaining:
            rendered.append(line)
            continue
        if normalized not in seen:
            rendered.append(f"{normalized}={_quote_env(remaining[normalized])}\n")
            seen.add(normalized)

    for key, value in remaining.items():
        if key not in seen:
            if rendered and not rendered[-1].endswith(("\n", "\r")):
                rendered.append("\n")
            rendered.append(f"{key}={_quote_env(value)}\n")

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=env_path.parent,
        prefix=f".{env_path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        handle.writelines(rendered)
        temp_name = handle.name
    try:
        os.replace(temp_name, env_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def _write_oauth_diagnostic(phase: str, *, error: str = "") -> None:
    """Persist only safe lifecycle state; never an OAuth code or credentials."""
    payload = {"phase": phase, "updated_at": int(time.time())}
    if error:
        payload["error"] = error[:500]
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=OAUTH_DIAGNOSTIC_PATH.parent,
        prefix=".feishu-oauth-", suffix=".tmp", delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False)
        temp_name = handle.name
    try:
        os.replace(temp_name, OAUTH_DIAGNOSTIC_PATH)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def _oauth_diagnostic() -> Mapping[str, Any]:
    try:
        data = json.loads(OAUTH_DIAGNOSTIC_PATH.read_text(encoding="utf-8"))
        return {
            "phase": str(data.get("phase") or ""),
            "updated_at": _as_int(str(data.get("updated_at") or "")) or None,
            "error": str(data.get("error") or ""),
        }
    except (OSError, ValueError, TypeError):
        return {}


@contextlib.contextmanager
def _refresh_lock(env_path: Path, timeout_s: float = 15.0) -> Iterator[None]:
    """Avoid two processes consuming the same one-use refresh token."""
    lock_path = env_path.with_suffix(env_path.suffix + ".refresh.lock")
    deadline = time.monotonic() + timeout_s
    acquired = False
    with _LOCK:
        while time.monotonic() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > timeout_s:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                time.sleep(0.1)
    if not acquired:
        raise FeishuOAuthError("Timed out waiting for the Feishu refresh-token lock")
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def _request_token(payload: Mapping[str, str], *, timeout_s: float = 15.0) -> Mapping[str, Any]:
    try:
        response = requests.post(
            OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/json; charset=utf-8"},
            json=dict(payload), timeout=timeout_s,
        )
        body = response.json()
    except requests.RequestException as exc:
        raise FeishuOAuthError(f"Feishu OAuth network error: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise FeishuOAuthError("Feishu OAuth returned non-JSON content") from exc
    if response.status_code != 200 or body.get("code") != 0:
        code = body.get("code", response.status_code)
        detail = str(body.get("error_description") or body.get("msg") or body.get("error") or "request failed")
        raise FeishuOAuthError(f"Feishu OAuth failed ({code}): {detail[:240]}")
    if not body.get("access_token"):
        raise FeishuOAuthError("Feishu OAuth response omitted access_token")
    return body


def _persist_token_response(response: Mapping[str, Any], *, env_path: Path, now: Optional[float] = None) -> TokenState:
    timestamp = int(now if now is not None else time.time())
    access = str(response["access_token"])
    refresh = str(response.get("refresh_token") or "")
    # Both the first authorization and every refresh must return the next
    # refresh token.  Keeping an old value would be unsafe because Feishu
    # refresh tokens are single-use.
    if not refresh:
        raise FeishuOAuthError(
            "Feishu OAuth response omitted refresh_token; ensure offline_access is approved and granted"
        )
    updates = {
        "FEISHU_UAT": access,
        "FEISHU_UAT_EXPIRES_AT": str(timestamp + _as_int(str(response.get("expires_in") or "0"))),
        "FEISHU_REFRESH_TOKEN": refresh,
        "FEISHU_REFRESH_TOKEN_EXPIRES_AT": str(
            timestamp + _as_int(str(response.get("refresh_token_expires_in") or "0"))
        ),
    }
    _atomic_update_env(env_path, updates)
    for key, value in updates.items():
        os.environ[key] = value
    return load_token_state(env_path)


def exchange_authorization_code(code: str, *, env_path: Path = DEFAULT_ENV_PATH, redirect_uri: str = "") -> TokenState:
    state = load_token_state(env_path)
    if not state.app_id or not state.app_secret:
        raise FeishuOAuthError("FEISHU_APP_ID and FEISHU_APP_SECRET are required for OAuth exchange")
    if not code.strip():
        raise FeishuOAuthError("authorization code is required")
    payload = {
        "grant_type": "authorization_code",
        "client_id": state.app_id,
        "client_secret": state.app_secret,
        "code": code.strip(),
    }
    effective_redirect = redirect_uri or state.redirect_uri
    if effective_redirect:
        payload["redirect_uri"] = effective_redirect
    return _persist_token_response(_request_token(payload), env_path=env_path)


def refresh_access_token(*, env_path: Path = DEFAULT_ENV_PATH) -> TokenState:
    state = load_token_state(env_path)
    if not state.app_id or not state.app_secret or not state.refresh_token:
        raise FeishuOAuthError(
            "FEISHU_APP_ID, FEISHU_APP_SECRET, and FEISHU_REFRESH_TOKEN are required for refresh"
        )
    payload = {
        "grant_type": "refresh_token",
        "client_id": state.app_id,
        "client_secret": state.app_secret,
        "refresh_token": state.refresh_token,
    }
    return _persist_token_response(_request_token(payload), env_path=env_path)


def ensure_valid_project_access_token(
    *, env_path: Path = DEFAULT_ENV_PATH, refresh_skew_s: int = ACCESS_EXPIRY_SKEW_S,
) -> str:
    """Return the current UAT or atomically rotate it when it is expiring."""
    state = load_token_state(env_path)
    if not _needs_refresh(state, skew_s=refresh_skew_s):
        if state.access_token:
            os.environ["FEISHU_UAT"] = state.access_token
        return state.access_token
    if not state.refresh_token:
        if state.access_token:
            raise FeishuOAuthError("FEISHU_UAT is expired/expiring and FEISHU_REFRESH_TOKEN is not configured")
        return ""
    with _refresh_lock(env_path):
        state = load_token_state(env_path)
        if not _needs_refresh(state, skew_s=refresh_skew_s):
            os.environ["FEISHU_UAT"] = state.access_token
            return state.access_token
        return refresh_access_token(env_path=env_path).access_token


def build_authorization_url(
    *, env_path: Path = DEFAULT_ENV_PATH, state: str = "", redirect_uri: str = "", scopes: str = "",
) -> str:
    config = load_token_state(env_path)
    effective_redirect = redirect_uri or config.redirect_uri
    effective_scopes = scopes or config.scopes
    if not config.app_id or not effective_redirect:
        raise FeishuOAuthError("FEISHU_APP_ID and FEISHU_OAUTH_REDIRECT_URI are required to build an authorization URL")
    scope_values = effective_scopes.split()
    required = {"offline_access", "search:docs:read"}
    missing = sorted(required - set(scope_values))
    if missing:
        raise FeishuOAuthError("FEISHU_OAUTH_SCOPES must include: " + ", ".join(missing))
    params = {
        "client_id": config.app_id,
        "redirect_uri": effective_redirect,
        "response_type": "code",
        "scope": " ".join(scope_values),
        "state": state or secrets.token_urlsafe(24),
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def _loopback_callback_parts(redirect_uri: str) -> tuple[str, int, str]:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise FeishuOAuthError(
            "Automatic callback only supports an http localhost/127.0.0.1 redirect URI"
        )
    if not parsed.port:
        raise FeishuOAuthError("The loopback redirect URI must include an explicit port")
    return parsed.hostname, parsed.port, parsed.path or "/"


def authorize_via_loopback(
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    redirect_uri: str,
    scopes: str,
    timeout_s: float = 300.0,
    open_browser: bool = True,
    on_authorization_ready: Optional[Callable[[str], None]] = None,
) -> TokenState:
    """Complete a browser OAuth flow on a configured local loopback callback.

    The callback must already be whitelisted in the Feishu application.  No
    configuration is written until Feishu returns a valid authorization code
    and the token exchange succeeds.
    """
    host, port, callback_path = _loopback_callback_parts(redirect_uri)
    if timeout_s <= 0:
        raise FeishuOAuthError("OAuth callback timeout must be positive")
    expected_state = secrets.token_urlsafe(24)
    authorization_url = build_authorization_url(
        env_path=env_path, state=expected_state, redirect_uri=redirect_uri, scopes=scopes,
    )
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != callback_path:
                self.send_error(404, "Unknown OAuth callback path")
                return
            values = parse_qs(parsed.query)
            if values.get("state", [""])[0] != expected_state:
                result["error"] = "OAuth callback state did not match the authorization request"
                body = "OAuth authorization rejected: state mismatch. You may close this window."
            elif values.get("error", [""])[0]:
                detail = values.get("error_description", values["error"])[0]
                result["error"] = f"Feishu authorization was denied: {detail}"
                body = "OAuth authorization was denied. You may close this window."
            elif values.get("code", [""])[0]:
                result["code"] = values["code"][0]
                body = "Authorization received. You may close this window and return to Codex."
            else:
                result["error"] = "OAuth callback did not include an authorization code"
                body = "OAuth callback did not include a code. You may close this window."
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer((host, port), CallbackHandler)
    server.timeout = 0.25
    _write_oauth_diagnostic("callback_listening")
    try:
        if on_authorization_ready is not None:
            on_authorization_ready(authorization_url)
        if open_browser and not webbrowser.open(authorization_url):
            raise FeishuOAuthError("Could not open the authorization URL in a browser")
        deadline = time.monotonic() + timeout_s
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if "error" in result:
        _write_oauth_diagnostic("callback_rejected", error=result["error"])
        raise FeishuOAuthError(result["error"])
    if "code" not in result:
        _write_oauth_diagnostic("callback_timeout")
        raise FeishuOAuthError("Timed out waiting for the Feishu OAuth callback")

    _write_oauth_diagnostic("exchanging_code")
    try:
        token_state = exchange_authorization_code(
            result["code"], env_path=env_path, redirect_uri=redirect_uri,
        )
    except FeishuOAuthError as exc:
        _write_oauth_diagnostic("token_exchange_failed", error=str(exc))
        raise
    _atomic_update_env(env_path, {
        "FEISHU_OAUTH_REDIRECT_URI": redirect_uri,
        "FEISHU_OAUTH_SCOPES": " ".join(scopes.split()),
    })
    _write_oauth_diagnostic("completed")
    return token_state


def token_status(*, env_path: Path = DEFAULT_ENV_PATH) -> dict[str, Any]:
    config = load_token_state(env_path)
    now = int(time.time())
    scopes: list[str] = []
    if config.access_token.count(".") == 2:
        try:
            payload = config.access_token.split(".")[1]
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            scopes = str(data.get("scope") or "").split()
        except (ValueError, TypeError, UnicodeDecodeError):
            pass
    result = {
        "app_id_present": bool(config.app_id),
        "app_secret_present": bool(config.app_secret),
        "redirect_uri_configured": bool(config.redirect_uri),
        "access_token_present": bool(config.access_token),
        "access_token_fingerprint": hashlib.sha256(config.access_token.encode()).hexdigest()[:12] if config.access_token else "",
        "access_expires_at": config.access_expires_at or None,
        "access_remaining_s": max(0, config.access_expires_at - now) if config.access_expires_at else None,
        "refresh_token_present": bool(config.refresh_token),
        "refresh_expires_at": config.refresh_expires_at or None,
        "search_docs_scope": "search:docs:read" in scopes,
        "offline_access_scope": "offline_access" in scopes,
    }
    diagnostic = _oauth_diagnostic()
    if diagnostic:
        result["last_oauth_diagnostic"] = diagnostic
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description="Manage Feishu OAuth v2 tokens without printing credentials")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("authorize-url")
    exchange = commands.add_parser("exchange-code")
    exchange.add_argument("--code", required=True)
    exchange.add_argument("--redirect-uri", default="")
    exchange.add_argument("--scopes", default="")
    commands.add_parser("refresh")
    browser_auth = commands.add_parser("authorize-browser")
    browser_auth.add_argument("--redirect-uri", required=True)
    browser_auth.add_argument("--scopes", required=True)
    browser_auth.add_argument("--timeout", type=float, default=300.0)
    browser_auth.add_argument("--no-open-browser", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "status":
            result: Mapping[str, Any] = token_status(env_path=args.env_file)
        elif args.command == "authorize-url":
            result = {"authorization_url": build_authorization_url(env_path=args.env_file)}
        elif args.command == "exchange-code":
            state = exchange_authorization_code(args.code, env_path=args.env_file, redirect_uri=args.redirect_uri)
            updates: dict[str, str] = {}
            if args.redirect_uri:
                updates["FEISHU_OAUTH_REDIRECT_URI"] = args.redirect_uri
            if args.scopes:
                updates["FEISHU_OAUTH_SCOPES"] = " ".join(args.scopes.split())
            if updates:
                _atomic_update_env(args.env_file, updates)
            _write_oauth_diagnostic("completed")
            result = token_status(env_path=args.env_file) | {"refreshed": bool(state.refresh_token)}
        elif args.command == "authorize-browser":
            def _show_authorization_url(url: str) -> None:
                print(json.dumps({"ok": True, "authorization_url": url}, ensure_ascii=False), flush=True)

            state = authorize_via_loopback(
                env_path=args.env_file,
                redirect_uri=args.redirect_uri,
                scopes=args.scopes,
                timeout_s=args.timeout,
                open_browser=not args.no_open_browser,
                on_authorization_ready=_show_authorization_url,
            )
            result = token_status(env_path=args.env_file) | {"refreshed": bool(state.refresh_token)}
        else:
            state = refresh_access_token(env_path=args.env_file)
            result = token_status(env_path=args.env_file) | {"refreshed": bool(state.access_token)}
    except FeishuOAuthError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
