from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.error import URLError
from urllib.parse import urlparse
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import (
    InvalidTokenError,
    PyJWKClient,
    PyJWKClientConnectionError,
    PyJWKClientError,
)

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
LOCAL_GUEST_USER_ID = "00000000-0000-4000-8000-000000000001"


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str


@lru_cache(maxsize=4)
def jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


def _auth_settings() -> tuple[str, str, str]:
    project_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    legacy_secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
    parsed = urlparse(project_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"})
    ):
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured. Set the Supabase project URL on the server.",
        )
    return project_url, legacy_secret, f"{project_url}/auth/v1"


def _decode_access_token(token: str) -> dict[str, object]:
    project_url, legacy_secret, issuer = _auth_settings()
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "HS256" and legacy_secret:
            return jwt.decode(
                token,
                legacy_secret,
                algorithms=["HS256"],
                audience="authenticated",
                issuer=issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        if algorithm not in {"ES256", "RS256"}:
            raise InvalidTokenError("Unsupported access-token signing algorithm.")
        signing_key = jwks_client(f"{project_url}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except PyJWKClientConnectionError as error:
        logger.warning("Could not retrieve Supabase signing keys.")
        raise HTTPException(
            status_code=503,
            detail="Could not reach Supabase to verify sign-in. Check the project URL and network.",
        ) from error
    except (InvalidTokenError, PyJWKClientError, ValueError) as error:
        logger.info("Rejected an invalid Supabase access token.")
        raise HTTPException(status_code=401, detail="Your sign-in has expired. Sign in with Google again.") from error
    except (TimeoutError, OSError, URLError) as error:
        logger.warning("Could not contact Supabase while verifying a sign-in.")
        raise HTTPException(
            status_code=503,
            detail="Could not verify sign-in with Supabase. Check the project URL and JWKS availability.",
        ) from error


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthenticatedUser:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials.strip()
    ):
        client_host = request.client.host if request and request.client else ""
        try:
            is_loopback = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            is_loopback = False
        if is_loopback and not os.getenv("RENDER_SERVICE_ID"):
            return AuthenticatedUser(id=LOCAL_GUEST_USER_ID, email="local-guest@localhost")
        raise HTTPException(status_code=401, detail="Sign in with Google to continue.")
    claims = _decode_access_token(credentials.credentials)
    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not isinstance(email, str):
        raise HTTPException(status_code=401, detail="The sign-in token has no valid user identity.")
    try:
        user_id = str(UUID(subject))
    except ValueError as error:
        raise HTTPException(status_code=401, detail="The sign-in token has no valid user identity.") from error
    return AuthenticatedUser(id=user_id, email=email)
