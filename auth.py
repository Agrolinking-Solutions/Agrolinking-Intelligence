"""
Google ID token verification for api.py.

The frontend already has its own login (Google Sign-In) — this does NOT
build a parallel login system. It verifies the same token Google already
issued the user at login, using Google's own public keys, so no shared
secret is needed from the frontend team at all. What IS needed from them:
the frontend must attach that token to protected requests as
`Authorization: Bearer <google_id_token>`.

Required env var:
  GOOGLE_CLIENT_ID — the OAuth Client ID the frontend's Google Sign-In
  uses. Not secret (it's normal for this to be visible in frontend code),
  but required here so a token issued for some OTHER app can't be reused
  against this API (the `aud` claim must match).

Usage in api.py:
    from fastapi import Depends
    from auth import get_current_user_email

    @app.get("/alerts/saved")
    def get_saved_alerts(email: str = Depends(get_current_user_email)):
        ...
"""

import os
from fastapi import Header, HTTPException

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")

try:
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
    _GOOGLE_AUTH_AVAILABLE = True
    _google_request = google_requests.Request()
except ImportError:
    _GOOGLE_AUTH_AVAILABLE = False
    _google_request = None

VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def verify_google_token(token: str) -> dict:
    """
    Verifies a Google ID token's signature, expiry, issuer, and audience.
    Returns the decoded claims (includes 'email', 'email_verified', 'sub',
    'name', ...) on success. Raises HTTPException(401) on any failure —
    never returns a usable identity for a bad token.
    """
    if not _GOOGLE_AUTH_AVAILABLE:
        raise HTTPException(status_code=500, detail="Server auth not configured (google-auth not installed)")
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Server auth not configured (GOOGLE_CLIENT_ID not set)")

    try:
        claims = google_id_token.verify_oauth2_token(token, _google_request, GOOGLE_CLIENT_ID)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired login token: {e}")

    if claims.get("iss") not in VALID_ISSUERS:
        raise HTTPException(status_code=401, detail="Token issuer is not Google")
    if not claims.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google account email is not verified")
    if not claims.get("email"):
        raise HTTPException(status_code=401, detail="Token has no email claim")

    return claims


async def get_current_user_email(authorization: str = Header(None)) -> str:
    """
    FastAPI dependency: extracts and verifies the bearer token, returns
    the caller's verified email. Raises 401 if the header is missing,
    malformed, or the token doesn't check out.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected: Authorization: Bearer <google_id_token>",
        )
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    claims = verify_google_token(token)
    return claims["email"]
