"""Fork: read what Twitch's client-integrity proof claims, to diagnose "failed integrity check" refusals.

The proof is a PASETO v4.public token: its payload is plain JSON followed by a 64-byte signature, so the
claims can be read without contacting Twitch. Nothing here verifies the signature or sends the token
anywhere, and a report never contains the token, the account or device IDs, or the IP address the proof
was issued to: only whether each of them matches what the miner's requests send.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from dateutil.parser import isoparse


PREFIX = "v4.public."
SIGNATURE_BYTES = 64


def read_claims(token: Any) -> dict[str, Any] | None:
    """The token's JSON claims, or None when it is not a readable v4.public token."""
    if not isinstance(token, str) or not token.startswith(PREFIX):
        return None
    payload = token[len(PREFIX):].split(".", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,16384}", payload):
        return None
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    except ValueError:
        return None
    # The signature follows the JSON; parse the JSON alone, and tolerate a signature of unexpected length.
    for candidate in (raw[:-SIGNATURE_BYTES], raw):
        text = candidate.decode("utf-8", "replace")
        start = text.find("{")
        if start < 0:
            continue
        try:
            claims, _end = json.JSONDecoder().raw_decode(text, start)
        except (ValueError, RecursionError):
            continue
        if isinstance(claims, dict):
            return claims
    return None


def _moment(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and len(value) <= 40:
        try:
            moment = isoparse(value)
        except (ValueError, OverflowError):
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _same(claimed: Any, actual: Any) -> bool | None:
    if claimed is None or actual is None:
        return None
    return str(claimed) == str(actual)


def proof_report(token: Any, headers: Mapping[str, str], user_id: int | None, now: datetime) -> dict[str, Any]:
    """What the proof claims, compared with the headers and account the miner's requests use."""
    claims = read_claims(token)
    if claims is None:
        shape = ".".join(token.split(".")[:2]) if isinstance(token, str) else ""
        return {"readable": False, "format": shape if re.fullmatch(r"v\d\.(public|local)", shape) else "unknown"}
    issued, expires, valid_from = (_moment(claims.get(name)) for name in ("iat", "exp", "nbf"))
    return {
        "readable": True,
        "bot_flag": _flag(claims.get("is_bad_bot")),
        "issued_at": issued.isoformat() if issued else None,
        "expires_at": expires.isoformat() if expires else None,
        "expired": expires <= now if expires else None,
        "not_yet_valid": valid_from > now if valid_from else None,
        "device_matches": _same(claims.get("device_id"), headers.get("x-device-id") or headers.get("device-id")),
        "account_matches": _same(claims.get("user_id"), user_id),
        "client_matches": _same(claims.get("client_id"), headers.get("client-id")),
        # Claim names only (never values), in case Twitch changes the format.
        "fields": sorted(str(name)[:40] for name in claims)[:30],
    }


def summarize(report: Any) -> str:
    """One line for the log: what is wrong with the proof, or that nothing visible is."""
    if not isinstance(report, Mapping) or not report.get("available"):
        return "no session proof available"
    if not report.get("readable"):
        return f"the proof could not be read (format: {report.get('format', 'unknown')})"
    problems = []
    if report.get("bot_flag"):
        problems.append("Twitch flagged it as a bot (is_bad_bot)")
    if report.get("expired"):
        problems.append("it has expired")
    if report.get("not_yet_valid"):
        problems.append("it is not valid yet (check the server clock)")
    for key, what in (("device_matches", "device"), ("account_matches", "account"), ("client_matches", "client ID")):
        if report.get(key) is False:
            problems.append(f"it was issued for a different {what}")
    if report.get("proxy_for_requests"):
        problems.append("requests use the proxy setting but the proof is issued without it (different IP)")
    if problems:
        return "; ".join(problems)
    if report.get("bot_flag") is None:
        return "no bot flag in the proof and nothing else wrong visible"
    return "not flagged as a bot and nothing else wrong visible"


def summarize_source(source: Any) -> str:
    """Summarize the proof of an object with session_proof() (the Twitch client); never raises."""
    getter = getattr(source, "session_proof", None)
    try:
        report = getter() if callable(getter) else None
    except Exception:  # a diagnostic must never change the outcome of the request it describes
        report = None
    return summarize(report)
