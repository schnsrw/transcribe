"""
casual_sst.auth
===============

JWT validation for the ``/ws/{meeting_id}?auth_token=...`` endpoint.

Activated when ``config.server.bypass_auth = false`` (the default in
``config/prod.yaml``). The validator runs on every WebSocket open;
failures close the socket with code 1008.

Two acceptance modes, evaluated in order:

  1. **Asymmetric ASAP** — Jitsi's standard. Public keys in PEM files
     are loaded from ``ASAP_PUB_KEYS_FOLDER``; each key is matched by
     ``kid``. The token's ``aud`` claim must appear in
     ``ASAP_PUB_KEYS_AUDS`` (comma-separated). This is what Jigasi
     emits in production.
  2. **HMAC shared secret** — fallback for simpler deployments. Set
     ``JWT_SECRET`` + ``JWT_ALGORITHM`` (default ``HS256``) +
     ``JWT_AUDIENCE``. No ``kid`` required; signature is checked against
     the secret.

If neither set of env vars is configured the validator raises at first
call so misconfigured prod stacks fail loudly at startup, not silently
under load.

Dependency: ``PyJWT[crypto]`` — installed in the Dockerfile and the
host venvs. We import lazily so the module is still importable on
dev stacks that have ``bypass_auth = true`` (most of them).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when a token fails validation. Caller closes the WS."""


@dataclass
class _ASAPConfig:
    folder: Path
    auds: list[str]


@dataclass
class _HMACConfig:
    secret: str
    algorithm: str
    audience: str | None


class JWTValidator:
    """Resolves whichever auth mode is configured via env and validates tokens."""

    def __init__(self) -> None:
        self._asap = self._load_asap()
        self._hmac = self._load_hmac()
        if not self._asap and not self._hmac:
            raise RuntimeError(
                "JWT auth required (config.server.bypass_auth=false) but no "
                "credentials configured. Set ASAP_PUB_KEYS_FOLDER+ASAP_PUB_KEYS_AUDS "
                "or JWT_SECRET in the environment."
            )

    @staticmethod
    def _load_asap() -> _ASAPConfig | None:
        folder = os.environ.get("ASAP_PUB_KEYS_FOLDER", "").strip()
        auds = os.environ.get("ASAP_PUB_KEYS_AUDS", "").strip()
        if not folder or not auds:
            return None
        path = Path(folder)
        if not path.is_dir():
            log.warning("ASAP_PUB_KEYS_FOLDER=%s does not exist; ignoring", folder)
            return None
        return _ASAPConfig(folder=path, auds=[a.strip() for a in auds.split(",") if a.strip()])

    @staticmethod
    def _load_hmac() -> _HMACConfig | None:
        secret = os.environ.get("JWT_SECRET", "").strip()
        if not secret:
            return None
        return _HMACConfig(
            secret=secret,
            algorithm=os.environ.get("JWT_ALGORITHM", "HS256").strip(),
            audience=os.environ.get("JWT_AUDIENCE", "").strip() or None,
        )

    def validate(self, token: str | None) -> dict:
        """Return the decoded JWT claims, or raise AuthError."""
        if not token:
            raise AuthError("missing auth_token")

        # Lazy import — keeps PyJWT optional for dev stacks with bypass_auth.
        try:
            import jwt
            from jwt import PyJWKClient  # noqa: F401 — only used in ASAP path
        except ImportError as e:  # pragma: no cover
            raise AuthError("PyJWT not installed; run `pip install PyJWT[crypto]`") from e

        if self._asap:
            return self._validate_asap(token, jwt)
        if self._hmac:
            return self._validate_hmac(token, jwt)
        raise AuthError("no validator configured")

    def _validate_asap(self, token: str, jwt) -> dict:
        # ASAP convention: header.kid points at a PEM in the keys folder.
        unverified = jwt.get_unverified_header(token)
        kid = unverified.get("kid", "")
        if not kid:
            raise AuthError("token missing kid header")
        # Sanitise — only allow [A-Za-z0-9._-]+.
        if any(c in kid for c in "/\\\x00"):
            raise AuthError("invalid kid")
        pem_path = self._asap.folder / f"{kid}.pem"
        if not pem_path.is_file():
            raise AuthError(f"unknown kid: {kid}")
        try:
            return jwt.decode(
                token,
                pem_path.read_bytes(),
                algorithms=["RS256", "RS512", "ES256", "ES384"],
                audience=self._asap.auds,
            )
        except Exception as e:
            raise AuthError(f"asap verify failed: {e!r}") from e

    def _validate_hmac(self, token: str, jwt) -> dict:
        try:
            return jwt.decode(
                token,
                self._hmac.secret,
                algorithms=[self._hmac.algorithm],
                audience=self._hmac.audience,
            )
        except Exception as e:
            raise AuthError(f"hmac verify failed: {e!r}") from e


# Lazy singleton — created on first use so config-only stacks
# (bypass_auth=true) never hit the "no credentials configured" check.
_VALIDATOR: JWTValidator | None = None


def get_validator() -> JWTValidator:
    global _VALIDATOR
    if _VALIDATOR is None:
        _VALIDATOR = JWTValidator()
    return _VALIDATOR
