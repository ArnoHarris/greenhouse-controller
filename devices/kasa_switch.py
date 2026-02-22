"""Kasa smart switch control for circulating fans (HS210 3-way switch).

Uses the Kasa local protocol directly (port 9999, XOR-encrypted JSON)
instead of the python-kasa library, avoiding asyncio and firmware timezone
compatibility issues.
"""

import json
import logging
import socket

log = logging.getLogger(__name__)

_PORT = 9999
_TIMEOUT = 5  # seconds
_XOR_KEY = 171


def _encrypt(payload: str) -> bytes:
    """Encrypt a JSON string using the Kasa XOR autokey cipher."""
    data = payload.encode("utf-8")
    header = len(data).to_bytes(4, "big")
    key = _XOR_KEY
    encrypted = bytearray()
    for b in data:
        key ^= b
        encrypted.append(key)
    return header + bytes(encrypted)


def _decrypt(data: bytes) -> str:
    """Decrypt Kasa XOR autokey cipher bytes to a JSON string."""
    key = _XOR_KEY
    result = bytearray()
    for b in data:
        result.append(key ^ b)
        key = b
    return result.decode("utf-8")


def _query(ip: str, payload: dict) -> dict:
    """Send a Kasa local protocol request and return the parsed response."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(_TIMEOUT)
        s.connect((ip, _PORT))
        s.sendall(_encrypt(json.dumps(payload)))

        # Read 4-byte length header, then exactly that many bytes
        header = b""
        while len(header) < 4:
            chunk = s.recv(4 - len(header))
            if not chunk:
                raise ConnectionError("Connection closed before header received")
            header += chunk

        expected = int.from_bytes(header, "big")
        data = b""
        while len(data) < expected:
            chunk = s.recv(expected - len(data))
            if not chunk:
                raise ConnectionError("Connection closed before response received")
            data += chunk

    return json.loads(_decrypt(data))


class KasaSwitch:
    """Controls a Kasa smart switch via the local Kasa protocol (port 9999).

    Synchronous raw socket implementation — no asyncio or python-kasa required.
    """

    def __init__(self, ip):
        self.ip = ip

    def read(self) -> dict:
        """Return current switch state. Raises on failure."""
        resp = _query(self.ip, {"system": {"get_sysinfo": {}}})
        relay_state = resp["system"]["get_sysinfo"]["relay_state"]
        return {"on": bool(relay_state)}

    def turn_on(self):
        """Turn switch on. Raises on failure."""
        _query(self.ip, {"system": {"set_relay_state": {"state": 1}}})

    def turn_off(self):
        """Turn switch off. Raises on failure."""
        _query(self.ip, {"system": {"set_relay_state": {"state": 0}}})
