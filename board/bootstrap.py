"""Run one temporary tuning session on the board over SSH standard I/O."""

import json
from pathlib import Path
import runpy
import signal
import socket
import sys
import tempfile


SCRIPTS = ("rkaiq_gray.py", "rkaiq_controls.py", "rkaiq_bridge.py", "rkaiq_media.py")


def phase(name):
    print(json.dumps({"phase": name}), flush=True)


def interrupt(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGHUP, interrupt)
signal.signal(signal.SIGTERM, interrupt)
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as lock:
        try:
            lock.bind("\0rkaiq-win-panel")
        except OSError:
            raise OSError("该板卡已有调参会话，请先退出另一客户端") from None
        phase("upload")
        line = sys.stdin.readline()
        if line:
            scripts = json.loads(line)
            session = tempfile.TemporaryDirectory(prefix="rkaiq-panel-", dir="/tmp")
            try:
                folder = Path(session.name)
                for name in SCRIPTS:
                    (folder / name).write_text(scripts[name], encoding="utf-8")
                sys.path.insert(0, session.name)
                phase("start")
                runpy.run_path(str(folder / "rkaiq_bridge.py"), run_name="__main__")
            finally:
                session.cleanup()
                phase("cleaned")
except KeyboardInterrupt:
    pass
except (OSError, ValueError, KeyError) as error:
    print(json.dumps({"fatal": str(error)}), flush=True)
