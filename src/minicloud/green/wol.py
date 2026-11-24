"""Wake-on-LAN: wake a sleeping laptop through its wired network card."""

import re
import socket


def magic_packet(mac: str) -> bytes:
    digits = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(digits) != 12:
        raise ValueError(f"not a MAC address: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(digits) * 16


def wake(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    """Send the magic packet. The sender must be on the same LAN as the sleeping laptop."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet(mac), (broadcast, port))
