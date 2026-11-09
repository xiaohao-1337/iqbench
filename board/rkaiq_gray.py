"""Control RKAIQ gray mode and recover the tuning socket on the verified HYEO build."""

import hashlib
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys

from rkaiq_controls import GRAY_CPSL, GRAY_OFF, GRAY_ON
from rkaiq_media import listener_for, owner, server_handles


HEADER = struct.Struct("<2sIiiI")
U32 = struct.Struct("<I")
GRAY_SET = 0x1250
GRAY_GET = 0x1251
SERVER_SHA256 = "bd7b443fd92bf853c71a1816f2ad8ea5ebeef88601b1fe1a8039f7ec321178c0"
LIBRARY_SHA256 = "798246f3294240872645eec0e5d51f62525655ff391450e35e6114b62e46747f"

# Layout of media_infos in the verified AArch64 executable.
MEDIA_COUNT = 16
GDB_RECOVERY = """
import gdb
import os

try:
    gdb.execute('set scheduler-locking off')
    gdb.execute('set unwindonsignal on')
    media = int(gdb.parse_and_eval('(char *)&media_infos + {} * 344'.format(media_index)))
    inferior = gdb.selected_inferior()
    for offset, size, expected in identity:
        actual = bytes(inferior.read_memory(media + offset, size)).split(b'\\x00', 1)[0]
        if actual != expected:
            raise gdb.GdbError('3A media context does not match the selected camera route')
    ctx = int(gdb.parse_and_eval('*(void **){}'.format(media + 328)))
    if not ctx:
        raise gdb.GdbError('The selected camera has no active AIQ context')
    if not os.path.lexists(socket_path):
        for enable in (0, 1):
            result = gdb.parse_and_eval(
                '((int (*)(void *, int)) &rk_aiq_uapi2_sysctl_tuning_enable)'
                '((void *){}, {})'.format(ctx, enable)
            )
            if int(result) != 0:
                raise gdb.GdbError('tuning_enable({}) returned {}'.format(enable, result))
finally:
    if gdb.selected_inferior().pid:
        gdb.execute('detach')
"""


def payload_hash(payload):
    # Gray mode requests and replies carry either zero bytes or one 32-bit value.
    factor = 0x5BD1E995
    mask = 0xFFFFFFFF
    value = 97 ^ len(payload)
    if payload:
        word = (U32.unpack(payload)[0] * factor) & mask
        word = ((word ^ (word >> 24)) * factor) & mask
        value = ((value * factor) & mask) ^ word
    value = ((value ^ (value >> 13)) * factor) & mask
    return value ^ (value >> 15)


def read_exact(connection, count):
    data = bytearray()
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            raise OSError("RKAIQ closed the socket before sending a complete reply")
        data.extend(chunk)
    return data


def request(path, command, payload=b""):
    size = HEADER.size + len(payload) + U32.size
    packet = HEADER.pack(b"RK", size, command, 0, len(payload))
    packet += payload + U32.pack(payload_hash(payload))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(path)
        connection.sendall(packet)
        magic, size, reply_command, result, length = HEADER.unpack(
            read_exact(connection, HEADER.size)
        )
        if (
            magic != b"RK"
            or reply_command != command
            or length not in (0, U32.size)
            or size != HEADER.size + length + U32.size
        ):
            raise OSError("Invalid RKAIQ reply header")
        reply = read_exact(connection, length)
        (checksum,) = U32.unpack(read_exact(connection, U32.size))
        if reply and checksum != payload_hash(reply):
            raise OSError("RKAIQ reply checksum mismatch")
        if result != 0:
            raise OSError(f"RKAIQ command 0x{command:04x} returned {result}")
        return reply


def gray_mode(path):
    reply = request(path, GRAY_GET)
    if len(reply) != U32.size:
        raise OSError("RKAIQ did not return a gray mode value")
    (value,) = U32.unpack(reply)
    if value not in (GRAY_CPSL, GRAY_OFF, GRAY_ON):
        raise OSError(f"RKAIQ returned an unknown gray mode value: {value}")
    return value


def verify_server_build(pid):
    process = Path("/proc") / pid
    if hashlib.sha256((process / "exe").read_bytes()).hexdigest() != SERVER_SHA256:
        raise OSError("The running rkaiq_3A_server does not match the verified build")

    library_path = "/usr/lib/librkaiq.so"
    library = process / "root" / library_path.lstrip("/")
    info = library.stat()
    expected_device = (os.major(info.st_dev), os.minor(info.st_dev))
    for line in (process / "maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or fields[5] != library_path:
            continue
        device = tuple(int(part, 16) for part in fields[3].split(":"))
        if device == expected_device and int(fields[4]) == info.st_ino:
            break
    else:
        raise OSError("Could not verify the RKAIQ library mapped by the running process")

    if hashlib.sha256(library.read_bytes()).hexdigest() != LIBRARY_SHA256:
        raise OSError("The running librkaiq.so does not match the verified build")


def recover_socket(path, route, pid):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise OSError("Automatic RKAIQ socket recovery requires root on the device")
    media = re.fullmatch(r"/dev/media(\d+)", route["media"])
    if not media or int(media[1]) >= MEDIA_COUNT:
        raise OSError("The selected media device is outside the verified 3A table")

    import fcntl

    # Serialize recovery so concurrent clients cannot toggle IPC twice.
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open("/run/rkaiq-gray-recovery.lock", flags, 0o600), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.lexists(path):
            return
        servers = server_handles()
        if owner(route, servers) != pid or not listener_for(route, path, servers[pid]):
            raise OSError("Could not confirm the selected camera and socket still belong to the 3A process")
        verify_server_build(pid)
        identity = [
            (192, 64, route["video"].encode()),
            (256, 32, route["sensor"].encode()),
            (288, 32, route["media"].encode()),
        ]
        script = (
            f"media_index = {int(media[1])}\nidentity = {identity!r}\n"
            f"socket_path = {path!r}\n{GDB_RECOVERY}"
        )
        print("Rebuilding RKAIQ tuning IPC with GDB. Capture may briefly pause.", file=sys.stderr)
        command = [
            "/usr/bin/gdb", "-nx", "-q", "-batch",
            "-iex", "set auto-load off", "-iex", "set debuginfod enabled off",
            "-p", pid, "-ex", f"python exec({script!r})",
        ]
        with subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        ) as debugger:
            try:
                output, _ = debugger.communicate(timeout=20)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                # Interrupt GDB and give its detach handler time to run.
                debugger.send_signal(signal.SIGINT)
                output, _ = debugger.communicate()
                raise OSError(f"GDB recovery was interrupted:\n{output.strip()}") from None
        if debugger.returncode != 0:
            raise OSError(f"GDB recovery failed:\n{output.strip()}")
