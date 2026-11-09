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

from rkaiq_controls import (
    AE_CONTROLS, AE_GROUPS, COLORS, CPROC, EXPOSURE, EXPOSURE_FRAMES, EXPOSURE_INFO,
    GAMMA_CURVE, GAMMA_PATH, GRAY_CPSL, GRAY_OFF, GRAY_ON, IQ_CONTROLS, MANUAL_EXPOSURE, PARAMETERS, WORK_MODE,
)
from rkaiq_gray import GRAY_SET, U32, gray_mode, read_exact, recover_socket, request
from rkaiq_media import (
    discover, iq_choices, iq_files, iq_name, listener_for, loaded_iq, owner, process_handles, server_handles,
)


HEADER = struct.Struct("<4siiIII")
MAGIC = b"R\xaa\xffK"
AE_COMM = "/ae_calib/CommCtrl"
AE_MANUAL = AE_COMM + "/AecManualCtrl"
CPSL = "/cpsl/param/enable"
IQ_PATHS = {
    **{key: spec.path for key, spec in IQ_CONTROLS.items()},
    "gamma_curve": GAMMA_CURVE,
    "ae_mode": AE_COMM + "/AecOpType",
    **{key: AE_MANUAL + spec.path.removeprefix(EXPOSURE + "/stManual")
       for key, spec in AE_CONTROLS.items() if spec.step is not None},
    "gray": PARAMETERS["gray"].path,
}
IQ_READS = {key: path.rsplit("/", 1)[0] if path.startswith((CPROC + "/", GAMMA_PATH + "/")) else path
            for key, path in IQ_PATHS.items() if key not in AE_CONTROLS and key != "gray"}
UNREACHABLE = (ConnectionError, TimeoutError, FileNotFoundError, PermissionError)
READ_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, struct.error)


@dataclass
class TuningSession:
    path: str
    iq: dict
    baseline: dict
    reference: dict
    scene_index: tuple[int, int]
    pid: str
    listener: str
    route: dict
    state: dict
    initial: dict = field(init=False)
    edited: set = field(default_factory=set)

    def __post_init__(self):
        self.initial = self.state.copy()


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


def optional_value(document, path):
    try:
        return iq_value(document, path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def read_parameters(path, reads):
    # Some SDKs reject an entire request when any path is unknown.
    try:
        return exchange(path, reads)
    except UNREACHABLE:
        return {}
    except READ_ERRORS:
        values = {}
        for key in reads if len(reads) > 1 else ():
            try:
                values.update(exchange(path, [key]))
            except UNREACHABLE:
                break
            except READ_ERRORS:
                continue
        return values


def iq_parameters(path, updates, available, edited=None):
    fields = {key: field for key, field in IQ_READS.items() if key in (updates or available)}
    if not fields:
        return {}
    reads = list(dict.fromkeys(fields.values()))
    if updates:
        changes = {IQ_PATHS[key]: int(value) if type(value) is bool else value for key, value in updates.items()}
        values = exchange(path, reads, changes)
    else:
        values = read_parameters(path, reads)
    state = {}
    for key, field in fields.items():
        value = values.get(field)
        if field != IQ_PATHS[key]:
            value = optional_value(value, IQ_PATHS[key][len(field):])
        if compatible_value(key, value):
            state[key] = normalize_value(key, value)
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


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def exposure_state(attributes, keys):
    state = {}
    for key in keys:
        spec = AE_CONTROLS[key]
        value = optional_value(attributes, spec.path.removeprefix(EXPOSURE))
        if compatible_value(key, value):
            state[key] = normalize_value(key, value)
    return state


def exposure_capabilities(path, attributes):
    mode = read_parameters(path, [WORK_MODE]).get(WORK_MODE)
    mode = optional_value(mode, "/mode")
    mode = mode if isinstance(mode, str) else None
    frames = EXPOSURE_FRAMES.get(mode)
    if frames is None:
        return mode, None, {}, {}
    group, times, gains = AE_GROUPS[frames > 1]
    prefix = "Lin" if frames == 1 else "Hdr"
    info = read_parameters(path, [EXPOSURE_INFO]).get(EXPOSURE_INFO)
    root = f"/{prefix}AeInfo"
    ranges, current = {}, {}
    for field, keys, real_field in (("stExpTimeRange", times, "integration_time"),
                                    ("stGainRange", gains, "analog_gain")):
        for frame, key in enumerate(keys[:frames]):
            index = "" if frames == 1 else f"/{frame}"
            suffix = f"{prefix}AeRange/{field}{index}"
            for source, route in ((info, f"{root}/{suffix}"), (attributes, f"/stAuto/{suffix}")):
                bounds = optional_value(source, route)
                if isinstance(bounds, dict):
                    low, high = bounds.get("Min"), bounds.get("Max")
                    if number(low) and number(high) and 0 < low <= high:
                        scale = AE_CONTROLS[key].scale
                        ranges[key] = (low * scale, high * scale)
                        break
            exp = "LinearExp" if frames == 1 else f"HdrExp/{frame}"
            value = optional_value(info, f"{root}/{exp}/{real_field}")
            if number(value) and value > 0:
                current[key] = value * AE_CONTROLS[key].scale
    return mode, group, ranges, current


def ae_parameters(path, updates, available, edited=None, iq_flags=()):
    attributes = (exchange(path, [EXPOSURE]) if updates else read_parameters(path, [EXPOSURE])).get(EXPOSURE)
    mode, group, ranges, current = exposure_capabilities(path, attributes)
    keys = ({"ae_mode"} | ranges.keys()) & available
    state = exposure_state(attributes, keys)
    if not updates:
        return state, ranges, mode

    entering = updates.get("ae_mode") == MANUAL_EXPOSURE and state.get("ae_mode") != MANUAL_EXPOSURE
    changes = {}
    seeded = {}
    if entering:
        frames = EXPOSURE_FRAMES.get(mode)
        if frames is None or len(ranges) != frames * 2 or not ranges.keys() <= available:
            raise ValueError("设备或所选 IQ 场景缺少当前工作模式的完整手动曝光参数")
        for key, (low, high) in ranges.items():
            value = current.get(key)
            if value is None:
                value = exposure_state(attributes, (key,)).get(key)
            if value is None:
                raise ValueError("设备未返回可用于切换手动模式的曝光值")
            seeded[key] = min(high, max(low, value))
    flags = set()
    for key, value in {**seeded, **updates}.items():
        spec = AE_CONTROLS[key]
        if key != "ae_mode":
            if key not in ranges or not (state.get("ae_mode") == MANUAL_EXPOSURE or entering):
                raise ValueError(f"当前工作模式无法调整 {spec.label}")
            low, high = ranges[key]
            if not low <= value <= high:
                raise ValueError(f"{spec.label} 超出设备范围 {low:.6g}–{high:.6g}")
            flag = "ManualTimeEn" if key.endswith("_time") else "ManualGainEn"
            flag_path = f"/{group}/{flag}"
            if optional_value(attributes, "/stManual" + flag_path) not in (0, 1) or flag_path not in iq_flags:
                raise ValueError("设备或所选 IQ 场景缺少当前工作模式的手动曝光开关")
            flags.add("/stManual" + flag_path)
            changes[EXPOSURE + "/stManual" + flag_path] = 1
        changes[spec.path] = value / spec.scale if spec.step is not None else value
    source = exchange(path, [EXPOSURE], changes)[EXPOSURE]
    state = exposure_state(source, keys)
    if any(optional_value(source, flag) != 1 for flag in flags):
        raise OSError("设备未确认手动曝光开关")
    confirm_updates(state, {**seeded, **updates}, edited)
    return state, ranges, mode


def compatible_value(key, value):
    if key == "gray":
        return type(value) is int and value in (GRAY_CPSL, GRAY_OFF, GRAY_ON)
    if key == "gamma_curve":
        return (isinstance(value, list) and len(value) == 49
                and all(type(point) is int and 0 <= point <= 4095 for point in value)
                and all(left <= right for left, right in zip(value, value[1:])))
    spec = PARAMETERS[key]
    if key in COLORS or key == "gamma_offset":
        return type(value) is int and spec.minimum <= value <= spec.maximum
    if spec.choices:
        return any(value == option for option, _ in spec.choices)
    if spec.switch:
        return type(value) in (bool, int) and value in (0, 1)
    return number(value)


def normalize_value(key, value):
    spec = PARAMETERS.get(key)
    if spec is None:
        return value
    if spec.switch:
        return bool(value)
    return value * spec.scale if spec.scale != 1 else value


def read_live(path, available):
    state = iq_parameters(path, {}, available)
    if "gray" in available:
        try:
            state["gray"] = gray_mode(path)
        except READ_ERRORS:
            pass
    ae, ranges, mode = ae_parameters(path, {}, available) if available & AE_CONTROLS.keys() else ({}, {}, None)
    state.update(ae)
    return {"state": state, "ranges": ranges, "work_mode": mode, "full_state": True}


def update_live(session, updates):
    if not isinstance(updates, dict):
        raise ValueError("无效的参数请求")
    if not updates:
        reply = read_live(session.path, session.baseline.keys())
        session.state = reply["state"]
        return reply
    groups, errors = {}, []
    for key, value in updates.items():
        spec = IQ_CONTROLS.get(key)
        if key not in session.state or not compatible_value(key, value) or (
                spec and spec.minimum is not None and not spec.minimum <= value <= spec.maximum):
            errors.append(f"{key} 当前不可调或超出范围")
            continue
        group = "ae" if key in AE_CONTROLS else "gray" if key == "gray" else IQ_PATHS[key].split("/", 2)[1]
        groups.setdefault(group, {})[key] = value
    reply = {"state": {}}
    for group, changes in groups.items():
        try:
            if group == "ae":
                flags = {f"/{name}/{flag}" for name, _, _ in AE_GROUPS for flag in ("ManualTimeEn", "ManualGainEn")
                         if optional_value(session.reference, f"{AE_MANUAL}/{name}/{flag}") in (0, 1)}
                state, ranges, mode = ae_parameters(session.path, changes, session.baseline.keys(), session.edited, flags)
                reply.update(ranges=ranges, work_mode=mode)
                for key in AE_CONTROLS:
                    session.state.pop(key, None)
            elif group == "gray":
                request(session.path, GRAY_SET, U32.pack(changes["gray"]))
                state = {"gray": gray_mode(session.path)}
                confirm_updates(state, changes, session.edited)
            else:
                state = iq_parameters(session.path, changes, session.state.keys(), session.edited)
            reply["state"].update(state)
        except READ_ERRORS as error:
            errors.append(str(error))
    session.state.update(reply["state"])
    if errors:
        reply["warning"] = " · ".join(errors)
    return reply


def iq_scenes(iq):
    base = optional_value(iq, "/main_scene/0/sub_scene/0/scene_isp30")
    if not isinstance(base, dict):
        raise ValueError("所选 IQ 文件缺少 ISP30 默认场景")
    scenes = {}
    for main_index, main in enumerate(iq["main_scene"]):
        if not isinstance(main, dict) or not isinstance(main.get("sub_scene"), list):
            continue
        for sub_index, sub in enumerate(main["sub_scene"]):
            if not isinstance(sub, dict) or not all(isinstance(item, str) and item
                                                   for item in (main.get("name"), sub.get("name"))):
                continue
            patch = sub.get("scene_isp30", {})
            if isinstance(patch, dict) and not any(key.startswith("scene_isp") and key != "scene_isp30" for key in sub):
                name = (main["name"], sub["name"])
                if name in scenes:
                    raise ValueError("IQ 文件含有重名场景，无法确认导出位置")
                scenes[name] = (main_index, sub_index)
    if not scenes:
        raise ValueError("所选 IQ 文件缺少可识别的场景")
    return base, scenes


def merge_scene(base, patch):
    # RKAIQ treats later scenes as merge patches over the first scene.
    result = dict(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_scene(result.get(key), value) if isinstance(value, dict) else value
    return result


def scene_entry(iq, position):
    main, sub = position
    return iq["main_scene"][main]["sub_scene"][sub]


def iq_value(scene, path):
    for field in path.strip("/").split("/"):
        scene = scene[int(field) if isinstance(scene, list) else field]
    return scene


def defaults_for(scene):
    defaults = {}
    for key, path in IQ_PATHS.items():
        value = optional_value(scene, path)
        if key == "gray":
            if type(value) not in (bool, int) or value not in (0, 1):
                continue
            value = GRAY_ON if value else GRAY_CPSL if optional_value(scene, CPSL) else GRAY_OFF
        elif not compatible_value(key, value):
            continue
        defaults[key] = normalize_value(key, value)
    return defaults


def replace_iq_value(patch, scene, path, value):
    original = iq_value(scene, path)
    if type(original) is bool and type(value) is int and value in (0, 1):
        value = bool(value)
    elif type(value) is bool and type(original) is int:
        value = int(value)
    if type(value) is float and math.isclose(original, value, rel_tol=1e-6, abs_tol=1e-9):
        return
    if original == value:
        return
    fields = path.strip("/").split("/")
    for field in fields[:-1]:
        if isinstance(scene, list):
            field = int(field)
        scene = scene[field]
        if isinstance(patch, dict) and field not in patch:
            # JSON merge patches replace arrays as a unit, so retain other frame values.
            patch[field] = deepcopy(scene) if isinstance(scene, list) else {}
        patch = patch[field]
    field = int(fields[-1]) if isinstance(patch, list) else fields[-1]
    patch[field] = value


def export_iq(session):
    if not session.edited:
        return session.iq
    live = read_live(session.path, session.edited)
    state = live["state"]
    if missing := session.edited - state.keys():
        raise ValueError("保存前无法确认以下已修改参数：" + "、".join(sorted(missing)))
    changed = {key for key in session.edited
               if not matches(state[key], session.initial.get(key, session.baseline[key]))}
    if not changed:
        return session.iq

    iq = deepcopy(session.iq)
    scene = session.reference
    patch = scene_entry(iq, session.scene_index).setdefault("scene_isp30", {})
    entered_manual = "ae_mode" in changed and state["ae_mode"] == MANUAL_EXPOSURE
    manual = session.edited & live["ranges"].keys() if entered_manual else changed & live["ranges"].keys()
    changed.update(manual)
    for key in changed:
        value = state[key]
        if key == "gray":
            if value != GRAY_ON and bool(optional_value(scene, CPSL)) != (value == GRAY_CPSL):
                raise ValueError("当前灰度模式与 IQ 的补光配置冲突，无法原样保存")
            value = value == GRAY_ON
        elif key in AE_CONTROLS and AE_CONTROLS[key].step:
            value /= AE_CONTROLS[key].scale
        replace_iq_value(patch, scene, IQ_PATHS[key], value)
    if manual:
        attrs = exchange(session.path, [EXPOSURE])[EXPOSURE]
        for group, times, gains in AE_GROUPS:
            for keys, flag in ((times, "ManualTimeEn"), (gains, "ManualGainEn")):
                if manual.intersection(keys):
                    value = iq_value(attrs, f"/stManual/{group}/{flag}")
                    replace_iq_value(patch, scene, f"{AE_MANUAL}/{group}/{flag}", value)
    return iq


def list_nodes():
    return [{key: route[key] for key in ("video", "media", "sensor")} for route in discover()]


def select_node(video, media, sensor, filename, scene_name=None, scene_lookup=False):
    route = next((route for route in discover() if route["video"] == video), None)
    if route is None or (route["media"], route["sensor"]) != (media, sensor):
        raise ValueError("所选 ISP 节点或 sensor 已变化，请重新扫描")
    if not route["sensor"]:
        raise ValueError("所选 ISP 没有唯一的已启用 sensor 输入，请检查板卡的 Media 链路")
    files = iq_files()
    if filename is None:
        return iq_choices(route, files), None
    iq_file = files.get(filename)
    if iq_file is None:
        raise ValueError("IQ 文件已变化，请重新扫描并选择")
    iq = json.loads(iq_file.read_text(encoding="utf-8-sig"))
    base, scenes = iq_scenes(iq)
    if scene_lookup:
        return {"iq_scenes": list(scenes)}, None
    if scene_name is not None and (not isinstance(scene_name, list) or not all(isinstance(name, str) for name in scene_name)):
        raise ValueError("无效的 IQ 场景")
    if scene_name is None:
        # Older desktops open normal/day without an explicit scene selection.
        scene = ("normal", "day") if ("normal", "day") in scenes else next(iter(scenes))
    else:
        scene = tuple(scene_name)
    if scene not in scenes:
        raise ValueError("IQ 场景已变化，请重新选择")
    position = scenes[scene]
    patch = scene_entry(iq, position).get("scene_isp30", {})
    reference = base if patch is base else merge_scene(base, patch)
    defaults = defaults_for(reference)
    if not defaults:
        raise ValueError(f"{iq_file.name} 没有面板支持的参数")
    servers = server_handles()
    pid = owner(route, servers)
    loaded = loaded_iq(route, pid, files.values())
    reply = {"state": {}, "defaults": defaults, "iq_file": filename, "loaded_iq": iq_name(loaded),
             "iq_scene": " / ".join(scene), "sensor": route["sensor"],
             "video": route["video"], "media": route["media"]}
    if not pid or route["camera_id"] is None:
        return {**reply, "offline": "所选节点没有可确认的 RKAIQ 3A 会话"}, None

    path = f'/tmp/UNIX.domain{route["camera_id"]}'
    listener = listener_for(route, path, servers[pid])
    # An unlinked socket can still appear as a listener in /proc/net/unix.
    if listener and not os.path.lexists(path):
        try:
            recover_socket(path, route, pid)
            listener = listener_for(route, path, process_handles(pid))
        except OSError as error:
            return {**reply, "offline": f"调参 socket 恢复失败 · {error}"}, None
    if not listener or not os.path.lexists(path):
        return {**reply, "offline": "所选 sensor 的调参 socket 不可用"}, None

    live = read_live(path, defaults.keys())
    if not live["state"]:
        return {**reply, "offline": "所选 RKAIQ 会话没有可读回的调参项"}, None
    session = TuningSession(path, iq, defaults, reference, position, pid, listener, route, live["state"].copy())
    return {**reply, **live}, session


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
                                              message.get("iq_file"), message.get("iq_scene"),
                                              message.get("scene_lookup", False))
            elif selected is None:
                raise ValueError("请先选择 ISP 节点")
            else:
                try:
                    handles = process_handles(selected.pid)
                except FileNotFoundError:
                    handles = set()
                if (not os.path.lexists(selected.path)
                        or selected.listener != listener_for(selected.route, selected.path, handles)):
                    selected = None
                    reply = {"error": "所选节点的 3A 会话已变化，请重新扫描", "reselect": True}
                elif message == {"export": True}:
                    reply = {"iq": export_iq(selected)}
                else:
                    reply = update_live(selected, message["set"])
        except (OSError, ValueError, TypeError, IndexError, KeyError, subprocess.TimeoutExpired) as error:
            reply = {"error": str(error)}
        print(json.dumps(reply), flush=True)
