"""Password-authenticated SSH transport for a single board tuning session."""

import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shlex
import socket
import sys
import tempfile
from threading import Event, Thread
from time import monotonic

import paramiko


BOARD_DIR = Path(__file__).resolve().parent.parent / "board"
SCRIPTS = ("rkaiq_gray.py", "rkaiq_controls.py", "rkaiq_bridge.py", "rkaiq_media.py")
USER_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RKAIQ Workbench"
MAX_REPLY = 64 * 1024 * 1024


def device_defaults():
    # Read workstation-specific defaults outside the portable package and source history.
    folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else BOARD_DIR.parent
    try:
        settings = json.loads((folder / "device.local.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(settings, dict):
        return {}
    return {key: value for key, value in settings.items() if isinstance(value, str)}


DEVICE_DEFAULTS = device_defaults()


def validate_login(host, user, password):
    if not re.fullmatch(r"[A-Za-z0-9:][A-Za-z0-9_.:%-]*", host):
        raise ValueError("请输入有效的设备 IP 或主机名")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
        raise ValueError("请输入有效的 SSH 用户名")
    if not password or any(char in password for char in "\0\r\n"):
        raise ValueError("请输入 SSH 密码")


def connect(client, host, user, password):
    known_hosts = USER_DIR / "known_hosts"
    keys = client.get_host_keys()
    if known_hosts.is_file():
        keys.load(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    options = dict(username=user, password=password, timeout=8, banner_timeout=8,
                   auth_timeout=8, channel_timeout=8, allow_agent=False, look_for_keys=False)
    try:
        client.connect(host, **options)
    except paramiko.BadHostKeyException:
        # Only the project's board address is allowed to rotate its host key automatically.
        if host != DEVICE_DEFAULTS.get("host"):
            raise OSError(f"设备 {host} 的 SSH 主机密钥已变化，请核对板卡身份") from None
        client.close()
        del keys[host]
        client.connect(host, **options)
    USER_DIR.mkdir(parents=True, exist_ok=True)
    client.save_host_keys(str(known_hosts))


def save_iq(target, iq):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(iq, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Cancelled(Exception):
    pass


class Replies:
    def __init__(self, channel):
        self.channel = channel
        self.buffer = bytearray()
        self.diagnostic = ""

    def read(self, deadline, stop=None):
        search_from = 0
        while monotonic() < deadline:
            if stop is not None and stop.is_set():
                raise Cancelled
            newline = self.buffer.find(b"\n", search_from)
            if newline >= 0:
                line = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                search_from = 0
                try:
                    reply = json.loads(line)
                except (ValueError, UnicodeError):
                    self.diagnostic = line.decode("utf-8", errors="replace").strip()[-600:]
                    continue
                if isinstance(reply, dict):
                    return reply
                continue
            search_from = len(self.buffer)
            try:
                data = self.channel.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise EOFError(self.diagnostic or "板端调参会话已关闭")
            self.buffer.extend(data)
            if len(self.buffer) > MAX_REPLY:
                raise OSError("板端响应超过允许大小")
        raise TimeoutError("等待板端响应超时")


class SshSession:
    def __init__(self, notify, finished):
        self.notify = notify
        self.finished = finished
        self.commands = Queue()
        self.stop = Event()

    def start(self, host, user, password):
        Thread(target=self.run, args=(host, user, password), daemon=True).start()

    def submit(self, operation, message, target=None):
        self.commands.put((operation, message, target))

    def close(self):
        self.stop.set()
        self.commands.put(None)

    def wait_for(self, replies, phase=None):
        deadline = monotonic() + 40
        while True:
            reply = replies.read(deadline, self.stop)
            if "fatal" in reply:
                raise OSError(reply["fatal"])
            if (phase is None and "phase" not in reply) or (phase is not None and reply.get("phase") == phase):
                return reply

    def cleanup(self, channel, replies):
        try:
            channel.shutdown_write()
            deadline = monotonic() + 28
            while replies.read(deadline).get("phase") != "cleaned":
                pass
        except (OSError, EOFError, paramiko.SSHException):
            return False
        return True

    def run(self, host, user, password):
        channel = None
        deployed = False
        cleaned = True
        error = ""
        with paramiko.SSHClient() as client:
            try:
                bootstrap = (BOARD_DIR / "bootstrap.py").read_text(encoding="utf-8")
                scripts = {name: (BOARD_DIR / name).read_text(encoding="utf-8") for name in SCRIPTS}
                connect(client, host, user, password)
                if self.stop.is_set():
                    raise Cancelled
                transport = client.get_transport()
                transport.set_keepalive(15)
                channel = transport.open_session(timeout=8)
                channel.set_combine_stderr(True)
                channel.settimeout(0.25)
                replies = Replies(channel)
                channel.exec_command("python3 -B -u -c " + shlex.quote(bootstrap))
                self.wait_for(replies, "upload")
                self.notify("phase", "upload")
                deployed = True
                channel.sendall((json.dumps(scripts) + "\n").encode("utf-8"))
                self.wait_for(replies, "start")
                self.notify("phase", "start")
                while not self.stop.is_set():
                    try:
                        command = self.commands.get(timeout=1)
                    except Empty:
                        if channel.closed or not transport.is_active():
                            raise EOFError("SSH 会话已断开，请重新连接")
                        continue
                    if command is None or self.stop.is_set():
                        break
                    operation, message, target = command
                    channel.sendall((json.dumps(message, allow_nan=False) + "\n").encode("utf-8"))
                    reply = self.wait_for(replies)
                    if target is not None and "iq" in reply:
                        try:
                            save_iq(target, reply["iq"])
                            reply = {"saved": str(target)}
                        except (OSError, ValueError) as exc:
                            reply = {"error": f"保存失败 · {exc}"}
                    self.notify(operation, reply)
            except Cancelled:
                pass
            except paramiko.AuthenticationException:
                error = "SSH 身份验证失败，请检查用户名与密码"
            except (OSError, EOFError, ValueError, paramiko.SSHException) as exc:
                error = str(exc)
            finally:
                if deployed:
                    cleaned = self.cleanup(channel, replies)
        self.finished(error, cleaned)
