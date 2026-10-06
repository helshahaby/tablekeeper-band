"""scrypt password hashing with per-user salts.

Hashes are stored as "scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>" so the
parameters travel with the hash through export/import.
"""

import hashlib
import hmac
import os
import re

N = 2 ** 13
R = 8
P = 1
DKLEN = 32

_HASH_RE = re.compile(r"scrypt\$([0-9]{1,7})\$([0-9]{1,3})\$([0-9]{1,3})\$([0-9a-f]{2,128})\$([0-9a-f]{2,256})")


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8", "surrogatepass"), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def is_valid_hash(stored):
    if not isinstance(stored, str):
        return False
    match = _HASH_RE.fullmatch(stored)
    if not match:
        return False
    n, r, p = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return (
        2 <= n <= 2 ** 20 and (n & (n - 1)) == 0 and 1 <= r <= 32 and 1 <= p <= 16
        and len(match.group(4)) % 2 == 0 and len(match.group(5)) % 2 == 0
        and 128 * r * n * 2 < 2 ** 31
    )


def verify_password(password, stored):
    if not is_valid_hash(stored):
        return False
    _, n, r, p, salt_hex, digest_hex = stored.split("$")
    n, r, p = int(n), int(r), int(p)
    expected = bytes.fromhex(digest_hex)
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8", "surrogatepass"), salt=bytes.fromhex(salt_hex), n=n, r=r, p=p,
            dklen=len(expected), maxmem=256 * 1024 * 1024,
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)
