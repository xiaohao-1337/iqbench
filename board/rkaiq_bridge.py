#!/usr/bin/env python3
"""Serve the Windows panel over an SSH session's standard input and output."""

import json
import math
import os
import socket
import struct
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass, field

from rkaiq_gray import BOARD_SOCKETS, GRAY_SET, MODES, U32, gray_mode, initial_gray_mode, read_exact, request
from rkaiq_controls import AE_CONTROLS, EXPOSURE, EXPOSURE_INFO, IQ_CONTROLS
from rkaiq_media import discover, iq_candidates, iq_preference, owner, process_handles, server_handles, socket_handle


HEADER = struct.Struct("<4siiIII")
MAGIC = b"R\xaa\xffK"
CPROC = "/cproc/param"
GAMMA = "/agamma_calib_v11/GammaTuningPara"
COLOR_LEVELS = ("brightness", "contrast", "saturation", "hue")
IQ_FIELDS = {
    **{key: (CPROC, key) for key in ("enable", *COLOR_LEVELS)},
    "gamma_enable": (GAMMA, "Gamma_en"),
    "gamma_offset": (GAMMA, "Gamma_out_offset"),
    "gamma_curve": (GAMMA, "Gamma_curve"),
}
SWITCHES = {"enable", "gamma_enable"}
LIMITS = {**dict.fromkeys(COLOR_LEVELS, 255), "gamma_offset": 4095}
AE_COMM = "/ae_calib/CommCtrl"
AE_LINEAR = AE_COMM + "/AecManualCtrl/LinearAE"
GRAY = "/colorAsGrey/param/enable"
CPSL = "/cpsl/param/enable"
IQ_PATHS = {
    **{key: f"{section}/{field}" for key, (section, field) in IQ_FIELDS.items()},
    **{key: spec.path for key, spec in IQ_CONTROLS.items()},
    "ae_mode": AE_COMM + "/AecOpType",
    "ae_time": AE_LINEAR + "/TimeValue",
    "ae_gain": AE_LINEAR + "/GainValue",
    "gray": GRAY,
}
MANUAL_EXPOSURE = "RK_AIQ_OP_MODE_MANUAL"
LINEAR_AE_FLAGS = {"ae_time": "ManualTimeEn", "ae_gain": "ManualGainEn"}


@dataclass
class TuningSession:
    path: str
    iq: dict
    baseline: dict
    pid: str
    listener: str
    route: dict
    edited: set = field(default_factory=set)


def iq_packet(command, operations):
    payload = json.dumps(operations, separators=(",", ":")).encode("ascii")
    return HEADER.pack(MAGIC, command, 0, 0, len(payload), len(payload)) + payload + bytes(4)


def exchange(path, reads, changes=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(4)
        connection.connect(path)
        if changes:
            connection.sendall(iq_packet(0, [
                {"op": "replace", "path": field, "value": value}
                for field, value in changes.items()
            ]))
        connection.sendall(iq_packet(1, [{"path": field} for field in reads]))
        magic, command, result, _, length, packet_length = HEADER.unpack(
            read_exact(connection, HEADER.size)
        )
        if magic != MAGIC or command != 1 or result or length != packet_length or not 0 < length <= 16384:
            raise OSError("Invalid RKAIQ parameter reply")
        items = json.loads(read_exact(connection, length))
        read_exact(connection, 4)

    return {item["path"]: item["value"] for item in items}


def iq_parameters(path, updates, available, read_all, edited):
    # Slider writes only read their own group. The explicit refresh reads the whole session.
    basic = {key: pair for key, pair in IQ_FIELDS.items()
             if key in available and (read_all or key in updates)}
    fields = {key: spec for key, spec in IQ_CONTROLS.items()
              if key in available and (read_all or key in updates)}
    changes = {}
    for key, value in updates.items():
        if key in IQ_FIELDS:
            changes["/".join(IQ_FIELDS[key])] = int(value) if key in SWITCHES else value
        else:
            changes[fields[key].path] = int(value) if type(value) is bool else value
    reads = [*dict.fromkeys(section for section, _ in basic.values()), *(spec.path for spec in fields.values())]
    values = exchange(path, reads, changes) if reads else {}
    state = {}
    for key, (section, field) in basic.items():
        value = (values.get(section) or {}).get(field)
        if value is not None:
            state[key] = bool(value) if key in SWITCHES else value
    for key, spec in fields.items():
        value = values.get(spec.path)
        if value is not None:
            state[key] = bool(value) if spec.step is None and not spec.choices else value
    confirm_updates(state, updates, edited)
    return state


def matches(actual, requested):
    if type(requested) is float:
        return isinstance(actual, (int, float)) and math.isclose(actual, requested, rel_tol=1e-5, abs_tol=1e-5)
    return actual == requested


def confirm_updates(state, updates, edited):
    confirmed = {key for key, value in updates.items() if matches(state.get(key), value)}
    if edited is not None:
        edited.update(confirmed)
    if rejected := updates.keys() - confirmed:
        raise OSError("设备回读与请求值不一致：" + ", ".join(sorted(rejected)))


def ae_parameters(path, updates, edited):
    changes = {}
    for key, value in updates.items():
        spec = AE_CONTROLS[key]
        if type(value) is bool:
            value = int(value)
        elif spec.step is not None:
            value /= spec.scale
        changes[spec.path] = value
    if updates.get("ae_mode") == MANUAL_EXPOSURE:
        current = exchange(path, [EXPOSURE])[EXPOSURE]
        if current["AecOpType"] != updates["ae_mode"]:
            linear = exchange(path, [EXPOSURE_INFO])[EXPOSURE_INFO]["LinAeInfo"]["LinearExp"]
            changes.setdefault(AE_CONTROLS["ae_time"].path, min(0.03, max(0.0001, linear["integration_time"])))
            changes.setdefault(AE_CONTROLS["ae_gain"].path, min(31.6, max(1, linear["analog_gain"])))
            changes[EXPOSURE + "/stManual/LinearAE/ManualTimeEn"] = 1
            changes[EXPOSURE + "/stManual/LinearAE/ManualGainEn"] = 1
    source = exchange(path, [EXPOSURE], changes)[EXPOSURE]
    state = {}
    for key, spec in AE_CONTROLS.items():
        value = source
        for name in spec.path.removeprefix(EXPOSURE + "/").split("/"):
            value = value[name]
        if spec.choices:
            state[key] = value
        elif spec.step is None:
            state[key] = bool(value)
        else:
            state[key] = value * spec.scale
    confirm_updates(state, updates, edited)
    return state


def validate_control(key, value, spec):
    if spec.choices:
        valid = any(value == option for option, _ in spec.choices)
    elif spec.step is None:
        valid = type(value) is bool
    else:
        valid = type(value) in (float, int) and spec.minimum <= value <= spec.maximum
    if not valid:
        raise ValueError(f"Invalid value for {key}")


def apply(updates, path, available, edited=None):
    advanced = {**IQ_CONTROLS, **AE_CONTROLS}
    if not isinstance(updates, dict) or updates.keys() - available:
        raise ValueError("所选 IQ 不支持请求中的参数")
    for key, value in updates.items():
        if key == "gray" and (type(value) is not int or value not in MODES.values()):
            raise ValueError("Invalid gray mode")
        if key in SWITCHES and type(value) is not bool:
            raise ValueError(f"{key} must be enabled or disabled")
        if key in LIMITS and (type(value) is not int or not 0 <= value <= LIMITS[key]):
            raise ValueError(f"{key} must be an integer in 0–{LIMITS[key]}")
        if key in advanced:
            validate_control(key, value, advanced[key])
        if key == "gamma_curve" and (
            not isinstance(value, list) or len(value) != 49
            or any(type(point) is not int or not 0 <= point <= 4095 for point in value)
            or any(left > right for left, right in zip(value, value[1:]))
        ):
            raise ValueError("Gamma requires 49 nondecreasing integer samples in 0–4095")

    state = iq_parameters(path, {key: value for key, value in updates.items()
                                 if key in IQ_FIELDS or key in IQ_CONTROLS}, available, not updates, edited)
    ae_updates = {key: value for key, value in updates.items() if key in AE_CONTROLS}
    if ae_updates or (not updates and available & AE_CONTROLS.keys()):
        state.update({key: value for key, value in ae_parameters(path, ae_updates, edited).items() if key in available})
    if "gray" in updates:
        request(path, GRAY_SET, U32.pack(updates["gray"]))
    if "gray" in updates or (not updates and "gray" in available):
        state["gray"] = gray_mode(path)
        confirm_updates(state, {"gray": updates["gray"]} if "gray" in updates else {}, edited)
    return state


def iq_scene(iq):
    for main in iq["main_scene"]:
        if main["name"] == "normal":
            for sub in main["sub_scene"]:
                if sub["name"] == "day":
                    return sub["scene_isp30"]
    raise ValueError("The IQ file has no normal/day ISP30 scene")


def iq_field(scene, path):
    parent = scene
    fields = path.strip("/").split("/")
    for field in fields[:-1]:
        parent = parent[int(field) if isinstance(parent, list) else field]
    return parent, int(fields[-1]) if isinstance(parent, list) else fields[-1]


def iq_value(scene, path):
    parent, field = iq_field(scene, path)
    return parent[field]


def defaults_for(scene):
    defaults = {}
    for key, path in IQ_PATHS.items():
        try:
            value = iq_value(scene, path)
        except (KeyError, IndexError):
            continue
        if key in SWITCHES or (key in IQ_CONTROLS and not (IQ_CONTROLS[key].choices or IQ_CONTROLS[key].step)):
            value = bool(value)
        elif key in AE_CONTROLS and AE_CONTROLS[key].step:
            value *= AE_CONTROLS[key].scale
        elif key == "gray":
            value = MODES["on"] if value else MODES["cpsl"] if iq_value(scene, CPSL) else MODES["off"]
        defaults[key] = value
    return defaults


def replace_iq_value(scene, path, value):
    parent, field = iq_field(scene, path)
    original = parent[field]
    if type(value) is bool and type(original) is int:
        value = int(value)
    if type(value) is float and math.isclose(original, value, rel_tol=1e-6, abs_tol=1e-9):
        return
    if original != value:
        parent[field] = value


def export_iq(session):
    if not session.edited:
        return session.iq
    available = session.baseline.keys()
    state = apply({}, session.path, available)
    changed = {key for key in session.edited if not matches(state[key], session.baseline[key])}
    if not changed:
        return session.iq

    # Switching into manual exposure also seeds shutter, gain and their enable flags.
    entered_manual = "ae_mode" in changed and state["ae_mode"] == MANUAL_EXPOSURE
    if entered_manual:
        changed.update(available & LINEAR_AE_FLAGS.keys())
    iq = deepcopy(session.iq)
    scene = iq_scene(iq)
    for key in changed:
        value = state[key]
        if key == "gray":
            if value != MODES["on"] and bool(iq_value(scene, CPSL)) != (value == MODES["cpsl"]):
                raise ValueError("当前灰度模式与 IQ 的补光配置冲突，无法原样保存")
            value = value == MODES["on"]
        elif key in AE_CONTROLS and AE_CONTROLS[key].step:
            value /= AE_CONTROLS[key].scale
        replace_iq_value(scene, IQ_PATHS[key], value)
    if entered_manual and changed & LINEAR_AE_FLAGS.keys():
        linear = exchange(session.path, [EXPOSURE])[EXPOSURE]["stManual"]["LinearAE"]
        for key, flag in LINEAR_AE_FLAGS.items():
            if key in changed:
                replace_iq_value(scene, f"{AE_LINEAR}/{flag}", linear[flag])
    return iq


def list_nodes():
    servers = server_handles()
    return [{"video": route["video"], "media": route["media"], "isp": route["isp"],
             "output": route["output"], "sensor": route["sensor"], "bound": bool(owner(route, servers))}
            for route in discover()]


def select_node(video, media, sensor, filename):
    route = next((route for route in discover() if route["video"] == video), None)
    if route is None or (route["media"], route["sensor"]) != (media, sensor):
        raise ValueError("所选 ISP 节点或 sensor 已变化，请重新扫描")
    if not route["sensor"]:
        raise ValueError("所选 ISP 没有唯一的已启用 sensor 输入，请检查板卡的 Media 链路")
    servers = server_handles()
    pid = owner(route, servers)
    candidates = iq_candidates(route)
    preferred, loaded = iq_preference(route, pid, candidates)
    loaded_name = loaded.name if loaded else None
    if filename is None:
        return {"iq_choices": [item.name for item in candidates],
                "preferred_iq": preferred.name if preferred else None, "loaded_iq": loaded_name}, None
    iq_file = next((item for item in candidates if item.name == filename), None)
    if iq_file is None:
        raise ValueError("IQ 文件已变化，请重新扫描并选择")
    iq = json.loads(iq_file.read_text(encoding="utf-8"))
    defaults = defaults_for(iq_scene(iq))
    if not defaults:
        raise ValueError(f"{iq_file.name} 没有面板支持的参数")
    reply = {"state": {}, "defaults": defaults, "iq_file": iq_file.name, "loaded_iq": loaded_name, "sensor": route["sensor"],
             "video": route["video"], "media": route["media"]}
    if not pid or route["camera_id"] is None:
        return {**reply, "offline": "所选节点没有可确认的 RKAIQ 3A 会话"}, None

    path = f'/tmp/UNIX.domain{route["camera_id"]}'
    handles = servers[pid]
    # An unlinked socket can still appear as a listener in /proc/net/unix.
    if not os.path.lexists(path):
        can_recover = (path in BOARD_SOCKETS and route["media"] == "/dev/media2"
                       and "_imx586 " in route["sensor"])
        if can_recover:
            try:
                initial_gray_mode(path, allow_recovery=True)
                handles = process_handles(pid)
            except OSError as error:
                return {**reply, "offline": f"调参 socket 恢复失败 · {error}"}, None
    listener = socket_handle(path, handles)
    if not listener or not os.path.lexists(path):
        return {**reply, "offline": "所选 sensor 的调参 socket 不可用"}, None

    state = apply({}, path, defaults.keys())
    if not state:
        return {**reply, "offline": "所选 RKAIQ 会话没有可读回的调参项"}, None
    return {**reply, "state": state}, TuningSession(path, iq, state, pid, listener, route)


def main():
    selected = None
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if message == {"list": True}:
                selected = None
                reply = {"nodes": list_nodes()}
            elif "select" in message:
                selected = None
                reply, selected = select_node(message["select"], message["media"], message["sensor"],
                                              message.get("iq_file"))
            elif selected is None:
                raise ValueError("请先选择 ISP 节点")
            else:
                try:
                    handles = process_handles(selected.pid)
                except FileNotFoundError:
                    handles = set()
                if (not os.path.lexists(selected.path)
                        or selected.listener != socket_handle(selected.path, handles)
                        or owner(selected.route, {selected.pid: handles}) != selected.pid):
                    selected = None
                    reply = {"error": "所选节点的 3A 会话已变化，请重新扫描", "reselect": True}
                elif message == {"export": True}:
                    reply = {"iq": export_iq(selected)}
                else:
                    updates = message["set"]
                    state = apply(updates, selected.path, selected.baseline.keys(), selected.edited)
                    reply = {"state": state}
        except (OSError, ValueError, TypeError, IndexError, KeyError, subprocess.TimeoutExpired) as error:
            reply = {"error": str(error)}
        print(json.dumps(reply), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
