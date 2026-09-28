import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 10

_hasher = PasswordHasher()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_ref(token_hash: str) -> str:
    """Lo único de un token que puede ir a un log: su hash truncado."""
    return token_hash[:12]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_request_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def format_code(code: str) -> str:
    return f"{code[:5]}-{code[5:]}"


def normalize_code(raw: str) -> str | None:
    code = re.sub(r"[\s-]", "", raw).upper()
    if len(code) != CODE_LEN or any(c not in CODE_ALPHABET for c in code):
        return None
    return code


def hash_code(link_id: int, code: str) -> str:
    return hashlib.sha256(f"{link_id}:{code}".encode()).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _mac(key: str, purpose: str, payload: bytes) -> bytes:
    return hmac.new(key.encode(), purpose.encode() + b"\x00" + payload, hashlib.sha256).digest()


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    link_id: int
    link_file_id: int
    grant_id: int | None
    expires: int


def sign_ticket(key: str, t: Ticket) -> str:
    payload = json.dumps([t.ticket_id, t.link_id, t.link_file_id, t.grant_id, t.expires],
                         separators=(",", ":")).encode()
    return f"{_b64(payload)}.{_b64(_mac(key, 'ticket', payload))}"


def verify_ticket(key: str, raw: str, now: float | None = None) -> Ticket | None:
    if len(raw) > 512 or raw.count(".") != 1:
        return None
    body, sig = raw.split(".")
    try:
        payload = _unb64(body)
        given = _unb64(sig)
    except ValueError:
        return None
    if not hmac.compare_digest(given, _mac(key, "ticket", payload)):
        return None
    try:
        tid, link_id, lf_id, grant_id, exp = json.loads(payload)
    except (ValueError, TypeError):
        return None
    ticket = Ticket(str(tid), int(link_id), int(lf_id),
                    None if grant_id is None else int(grant_id), int(exp))
    if ticket.expires <= (time.time() if now is None else now):
        return None
    return ticket


def csrf_token(key: str, user: str) -> str:
    return _b64(_mac(key, "csrf", user.encode()))


def check_csrf(key: str, user: str, given: str) -> bool:
    return hmac.compare_digest(given.encode(), csrf_token(key, user).encode())
