"""Run one temporary tuning session on the board over SSH standard I/O."""

import json
from pathlib import Path
import re
import signal
import socket
import sys
import tempfile


def phase(name):
    print(json.dumps({"phase": name}), flush=True)


def interrupt(*_):
    raise KeyboardInterrupt


def serve():
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as lock:
        try:
            lock.bind("\0rkaiq-win-panel")
        except OSError:
            raise OSError("该板卡已有调参会话，请先退出另一客户端") from None
        phase("upload")
        session = None
        try:
            if line := sys.stdin.readline():
                scripts = json.loads(line)
                session = tempfile.TemporaryDirectory(prefix="rkaiq-panel-", dir="/tmp")
                folder = Path(session.name)
                for name, source in scripts.items():
                    if not re.fullmatch(r"rkaiq_[a-z_]+\.py", name):
                        raise ValueError("无效的板端脚本文件名")
                    (folder / name).write_text(source, encoding="utf-8")
                sys.path.insert(0, session.name)
                from rkaiq_bridge import main

                phase("start")
                main()
        finally:
            if session is not None:
                session.cleanup()
            phase("cleaned")


for sig in (signal.SIGHUP, signal.SIGTERM):
    signal.signal(sig, interrupt)
try:
    serve()
except KeyboardInterrupt:
    pass
except (OSError, ValueError, TypeError, AttributeError, ImportError) as error:
    print(json.dumps({"fatal": str(error)}), flush=True)
