#!/usr/bin/env python3
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


HEADER = struct.Struct("<2sIiiI")
U32 = struct.Struct("<I")
GRAY_SET = 0x1250
GRAY_GET = 0x1251
MODES = {"cpsl": 0, "off": 1, "on": 2}
BOARD_SOCKETS = ("/tmp/UNIX.domain1", "/tmp/UNIX.domain2")
SERVER_SHA256 = "bd7b443fd92bf853c71a1816f2ad8ea5ebeef88601b1fe1a8039f7ec321178c0"
LIBRARY_SHA256 = "798246f3294240872645eec0e5d51f62525655ff391450e35e6114b62e46747f"

# media_infos has 16 entries of 344 bytes in the verified AArch64 executable.
# Entry 2 contains the media device path at offset 288 and the AIQ context at 328.
GDB_RECOVERY = """
import gdb

try:
    gdb.execute('set scheduler-locking off')
    gdb.execute('set unwindonsignal on')
    media = int(gdb.parse_and_eval('(char *)&media_infos + 2 * 344'))
    inferior = gdb.selected_inferior()
    path = bytes(inferior.read_memory(media + 288, 32)).split(b'\\x00', 1)[0]
    ctx = int(gdb.parse_and_eval('*(void **){}'.format(media + 328)))
    if path != b'/dev/media2' or not ctx:
        raise gdb.GdbError('Could not validate the active /dev/media2 AIQ context')
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
    if value not in MODES.values():
        raise OSError(f"RKAIQ returned an unknown gray mode value: {value}")
    return value


def bound_socket():
    result = subprocess.run(["pidof", "rkaiq_3A_server"], capture_output=True, text=True)
    pids = result.stdout.split()
    if len(pids) != 1:
        raise OSError("Expected one running rkaiq_3A_server")
    sensors = set()
    for fd in (Path("/proc") / pids[0] / "fd").iterdir():
        try:
            device = os.readlink(fd)
        except FileNotFoundError:
            continue
        if not device.startswith("/dev/v4l-subdev"):
            continue
        name = (Path("/sys/class/video4linux") / Path(device).name / "name").read_text()
        match = re.match(r"m0([12])_[bf]_imx586\b", name)
        if match:
            sensors.add(match[1])
    if len(sensors) != 1:
        raise OSError("Could not identify the IMX586 camera owned by rkaiq_3A_server")
    return f"/tmp/UNIX.domain{sensors.pop()}"


def verified_server_pid():
    result = subprocess.run(["pidof", "rkaiq_3A_server"], capture_output=True, text=True)
    pids = result.stdout.split()
    if result.returncode != 0 or len(pids) != 1 or not pids[0].isdecimal():
        raise OSError("Automatic recovery requires exactly one rkaiq_3A_server process")

    process = Path("/proc") / pids[0]
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
    return pids[0]


def recover_socket(path):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise OSError("Automatic RKAIQ socket recovery requires root on the device")

    import fcntl

    # Serialize recovery so concurrent clients cannot toggle IPC twice.
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open("/run/rkaiq-gray-recovery.lock", flags, 0o600), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.lexists(path):
            return
        if bound_socket() != path:
            raise OSError("The requested socket does not belong to the active 3A camera")
        pid = verified_server_pid()
        print("Rebuilding RKAIQ tuning IPC with GDB. Capture may briefly pause.", file=sys.stderr)
        command = [
            "/usr/bin/gdb", "-nx", "-q", "-batch",
            "-iex", "set auto-load off", "-iex", "set debuginfod enabled off",
            "-p", pid, "-ex", f"python exec({GDB_RECOVERY!r})",
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


def initial_gray_mode(path, allow_recovery):
    try:
        return gray_mode(path)
    except FileNotFoundError:
        if not allow_recovery or path not in BOARD_SOCKETS:
            raise
    recover_socket(path)
    return gray_mode(path)
