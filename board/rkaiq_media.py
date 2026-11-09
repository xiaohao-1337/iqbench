"""Discover Rockchip ISP routes and resolve their sensor calibration files."""

from collections import defaultdict
import json
import os
from pathlib import Path
import re
import subprocess


ISP = "rkisp-isp-subdev"
MAINPATH = "rkisp_mainpath"
IQ_DIR = Path("/etc/iqfiles")
SENSOR = re.compile(r"(m(\d+)_[a-z]_)?([\w.-]+)(?:\s|$)")


def topology(text):
    model = driver = name = ""
    nodes, sensors, incoming = {}, set(), defaultdict(list)
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("model "):
            model = line.split(maxsplit=1)[1]
        elif driver_field := re.fullmatch(r"driver\s+(\S+)", line):
            driver = driver_field[1]
        elif entity := re.match(r"- entity \d+: (.+) \(\d+ pads?,", line):
            name = entity[1]
        elif line.startswith("device node name "):
            nodes[name] = line.split()[-1]
        elif "subtype Sensor" in line:
            sensors.add(name)
        elif pad := re.match(r"pad(\d+):", line):
            sink_pad = int(pad[1])
        elif link := re.match(r'<- "(.+)":\d+ \[([^\]]+)\]', line):
            if "ENABLED" in {flag.strip() for flag in link[2].split(",")}:
                incoming[name].append((link[1], sink_pad))
    return driver, model, nodes, sensors, incoming


def routes(media):
    cif_sensors = defaultdict(set)
    for driver, model, nodes, sensors, incoming in media.values():
        if driver.startswith("rkcif"):
            linked = {source for links in incoming.values() for source, _ in links}
            cif_sensors[model.replace("_", "-")].update(
                (name, nodes[name]) for name in sensors & linked if name in nodes
            )

    for path, (driver, model, nodes, sensors, incoming) in media.items():
        if not driver.startswith("rkisp") or ISP not in nodes or MAINPATH not in nodes:
            continue
        upstream = [source for source, pad in incoming[ISP] if pad == 0]
        visited, candidates = set(), set()
        while upstream:
            source = upstream.pop()
            if source in visited:
                continue
            visited.add(source)
            if source in sensors and source in nodes:
                candidates.add((source, nodes[source]))
            candidates.update(cif_sensors[source.replace("_", "-")])
            upstream.extend(name for name, _ in incoming[source])
        sensor, sensor_node = next(iter(candidates)) if len(candidates) == 1 else ("", "")
        identity = SENSOR.match(sensor)
        owner_nodes = {nodes[name] for name in (ISP, "rkisp-statistics", "rkisp-input-params") if name in nodes}
        yield {"video": nodes[MAINPATH], "media": path, "isp": model, "output": MAINPATH,
               "sensor": sensor, "sensor_node": sensor_node, "owner_nodes": owner_nodes,
               "camera_id": int(identity[2]) if identity and identity[2] is not None else None}


def discover():
    media = {}
    for device in sorted(Path("/dev").glob("media[0-9]*")):
        if not re.fullmatch(r"media\d+", device.name):
            continue
        result = subprocess.run(["media-ctl", "-d", str(device), "-p"],
                                capture_output=True, text=True, timeout=3)
        if result.returncode:
            if not device.exists():
                continue
            raise OSError(f"读取 {device} 失败：{result.stderr.strip() or result.stdout.strip()}")
        media[str(device)] = topology(result.stdout)
    return list(routes(media))


def process_handles(pid):
    handles = set()
    for fd in (Path("/proc") / pid / "fd").iterdir():
        try:
            handles.add(os.readlink(fd))
        except FileNotFoundError:
            continue
    return handles


def server_handles():
    result = subprocess.run(["pidof", "rkaiq_3A_server"], capture_output=True, text=True, timeout=3)
    servers = {}
    for pid in result.stdout.split():
        try:
            servers[pid] = process_handles(pid)
        except FileNotFoundError:
            continue
    return servers


def owner(route, servers):
    owners = [pid for pid, handles in servers.items()
              if route["sensor_node"] in handles and route["owner_nodes"] & handles]
    return owners[0] if len(owners) == 1 else None


def socket_handle(path, handles):
    for line in Path("/proc/net/unix").read_text().splitlines():
        fields = line.split()
        if len(fields) == 8 and fields[7] == path and int(fields[3], 16) & 0x10000:
            handle = f"socket:[{fields[6]}]"
            if handle in handles:
                return handle
    return None


def iq_candidates(route):
    sensor = route["sensor"]
    model = SENSOR.match(sensor)[3]
    exact = IQ_DIR / f"{sensor}.json"
    candidates = sorted(p for p in IQ_DIR.glob("*.json") if p.is_file()
                        and (p == exact or p.stem == model or p.stem.startswith(model + "_")))
    if not candidates:
        raise ValueError(f"/etc/iqfiles 中没有找到 {sensor} 的 JSON")
    return candidates


def iq_preference(route, pid, candidates):
    """Keep the full sensor candidate list and distinguish the logged file from a guess."""
    if pid:
        logged = logged_iq(route["camera_id"], pid, candidates)
        if logged:
            return logged, logged

    sensor = route["sensor"]
    model = SENSOR.match(sensor)[3]
    of_node = Path("/sys/class/video4linux") / Path(route["sensor_node"]).name / "device/of_node"
    try:
        module, lens = ((of_node / f"rockchip,camera-module-{key}").read_bytes().rstrip(b"\0").decode()
                        for key in ("name", "lens-name"))
    except OSError:
        pass
    else:
        named = IQ_DIR / f"{model}_{module}_{lens}.json"
        if named in candidates:
            return named, None
    for named in (IQ_DIR / f"{sensor}.json", IQ_DIR / f"{model}.json"):
        if named in candidates:
            return named, None
    return (candidates[0] if len(candidates) == 1 else None), None


def logged_iq(camera_id, pid, candidates):
    # Match this camera ID and process lifetime, including logs relayed by systemd.
    try:
        stat = (Path("/proc") / pid / "stat").read_text()
        started = int(stat.rsplit(")", 1)[1].split()[19]) * 1_000_000 // os.sysconf("SC_CLK_TCK")
        result = subprocess.run(
            ["journalctl", "-b", "-u", "rkaiq_3A.service", "-g", "init success. iq:",
             "-n", "80", "-o", "json", "--no-pager"], capture_output=True, text=True, timeout=4
        )
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None
    pattern = rf'\bcid\[{camera_id}\].*?init success\. iq:\s*(/etc/iqfiles/.*?\.json)(?:\s|$)'
    for line in reversed(result.stdout.splitlines()):
        try:
            record = json.loads(line)
            timestamp = int(record.get("__MONOTONIC_TIMESTAMP", 0))
            message = record.get("MESSAGE", "")
        except (AttributeError, TypeError, ValueError):
            continue
        if timestamp + 100_000 >= started and isinstance(message, str) and (match := re.search(pattern, message)):
            logged = Path(match[1])
            if logged in candidates:
                return logged
    return None
