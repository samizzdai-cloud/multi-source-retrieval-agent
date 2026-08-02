# Feishu OAuth User Access Token lifecycle

The retrieval runtime now obtains its default Feishu UAT through
`feishu_read_tool.oauth_tokens`.  It keeps the project `.env` as the single
source of truth and never prints token values.

## Dependency chain

`build_retrieve_agent()` -> `_build_feishu_source_agent()` ->
`_resolve_feishu_uat()` -> `ensure_valid_project_access_token()` -> Feishu
Doc/Table/MCP clients.

An explicit `SourceConfig.feishu_uat` remains caller-owned and is never
overwritten or refreshed by this mechanism.  The standard `.env` path refreshes
an access token five minutes before expiry.  One cross-process lock and an
atomic `.env` replacement protect Feishu's one-use refresh-token rotation.

## One-time application configuration

In the Feishu application console, configure the OAuth redirect URL and ensure
the user has authorized every scope needed by this project.  The authorization
request must include at least `offline_access` and `search:docs:read`; retain
the document, wiki, drive, table, and sheet scopes already required by the
enabled source features.

Add these non-token settings to the project `.env` (do not commit it):

```dotenv
FEISHU_APP_ID="cli_xxx"
FEISHU_APP_SECRET="your-app-secret"
FEISHU_OAUTH_REDIRECT_URI="http://127.0.0.1:8765/callback"
FEISHU_OAUTH_SCOPES="offline_access search:docs:read ...existing granted scopes..."
```

Then generate a browser authorization URL:

```powershell
python -m feishu_read_tool.oauth_tokens authorize-url
```

For the configured loopback URL, use the one-command local flow instead.  It
starts a temporary callback listener, opens the system browser, validates the
OAuth state, exchanges the code, and writes `.env` only after a successful
exchange:

```powershell
python -m feishu_read_tool.oauth_tokens authorize-browser --redirect-uri "http://127.0.0.1:8765/callback" --scopes "offline_access search:docs:read"
```

After Feishu redirects to the configured callback, exchange the short-lived
`code` once:

```powershell
python -m feishu_read_tool.oauth_tokens exchange-code --code "<authorization-code>"
```

The command atomically writes and rotates these generated entries: `FEISHU_UAT`,
`FEISHU_UAT_EXPIRES_AT`, `FEISHU_REFRESH_TOKEN`, and
`FEISHU_REFRESH_TOKEN_EXPIRES_AT`.  A missing returned refresh token is treated
as failure, so an old one-use token can never silently remain in `.env`.

Use the following safe diagnostic at any time; it only returns presence,
expiry, scope flags, and a non-reversible access-token fingerprint:

```powershell
python -m feishu_read_tool.oauth_tokens status
```
