import platform
import socket
import time
import json
import threading
import subprocess
import os
from datetime import datetime, timezone

import psutil
from flask import Flask, render_template, jsonify
from flask_cors import CORS

try:
    import cpuinfo
    CPUINFO_AVAILABLE = True
except ImportError:
    CPUINFO_AVAILABLE = False

try:
    import wmi
    WMI_AVAILABLE = True
except ImportError:
    WMI_AVAILABLE = False

try:
    import GPUtil
    GPUTIL_AVAILABLE = True
except ImportError:
    GPUTIL_AVAILABLE = False

app = Flask(__name__)
CORS(app)

wmi_conn = None
if WMI_AVAILABLE:
    try:
        wmi_conn = wmi.WMI()
    except Exception:
        wmi_conn = None

speedtest_cache = {"result": None, "timestamp": 0}


def get_cpu_info():
    info = {
        "usage_percent": psutil.cpu_percent(interval=0.5),
        "usage_per_core": psutil.cpu_percent(interval=0, percpu=True),
        "count_physical": psutil.cpu_count(logical=False),
        "count_logical": psutil.cpu_count(logical=True),
        "freq": None,
        "times": None,
    }
    freq = psutil.cpu_freq()
    if freq:
        info["freq"] = {
            "current": round(freq.current, 0),
            "min": round(freq.min, 0) if freq.min else None,
            "max": round(freq.max, 0) if freq.max else None,
        }
    times = psutil.cpu_times_percent(interval=0)
    info["times"] = {
        "user": round(times.user, 1),
        "system": round(times.system, 1),
        "idle": round(times.idle, 1),
        "interrupt": round(getattr(times, "interrupt", 0), 1),
        "dpc": round(getattr(times, "dpc", 0), 1),
    }
    if CPUINFO_AVAILABLE:
        try:
            ci = cpuinfo.get_cpu_info()
            info["brand"] = ci.get("brand_raw", "Unknown")
            info["arch"] = ci.get("arch_string_raw", "Unknown")
            info["bits"] = ci.get("bits", 0)
            info["hz_advertised"] = ci.get("hz_advertised_friendly", "Unknown")
        except Exception:
            pass
    return info


def get_memory_info():
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total": vm.total,
        "available": vm.available,
        "used": vm.used,
        "percent": vm.percent,
        "cached": getattr(vm, "cached", 0),
        "buffers": getattr(vm, "buffers", 0),
        "swap_total": swap.total,
        "swap_used": swap.used,
        "swap_percent": swap.percent,
    }


def get_disk_info():
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": usage.percent,
            })
        except PermissionError:
            continue
    io = psutil.disk_io_counters()
    io_info = None
    if io:
        io_info = {
            "read_bytes": io.read_bytes,
            "write_bytes": io.write_bytes,
            "read_count": io.read_count,
            "write_count": io.write_count,
            "read_time": io.read_time,
            "write_time": io.write_time,
        }
    return {"partitions": disks, "io": io_info}


def get_network_info():
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    io = psutil.net_io_counters()
    interfaces = []
    for name, addr_list in addrs.items():
        iface = {"name": name, "addresses": [], "is_up": False, "speed": 0, "mtu": 0}
        if name in stats:
            iface["is_up"] = stats[name].isup
            iface["speed"] = stats[name].speed
            iface["mtu"] = stats[name].mtu
        for addr in addr_list:
            iface["addresses"].append({
                "family": str(addr.family),
                "address": addr.address,
                "netmask": addr.netmask,
            })
        interfaces.append(iface)
    io_info = {
        "bytes_sent": io.bytes_sent,
        "bytes_recv": io.bytes_recv,
        "packets_sent": io.packets_sent,
        "packets_recv": io.packets_recv,
        "errin": io.errin,
        "errout": io.errout,
    }
    return {"interfaces": interfaces, "io": io_info}


def get_gpu_info():
    gpus = []
    if GPUTIL_AVAILABLE:
        try:
            gpu_list = GPUtil.getGPUs()
            for gpu in gpu_list:
                gpus.append({
                    "id": gpu.id,
                    "name": gpu.name,
                    "load": round(gpu.load * 100, 1) if gpu.load else 0,
                    "memory_total": gpu.memoryTotal,
                    "memory_used": gpu.memoryUsed,
                    "memory_free": gpu.memoryFree,
                    "temperature": gpu.temperature,
                })
        except Exception:
            pass
    if not gpus and wmi_conn:
        try:
            for gpu in wmi_conn.Win32_VideoController():
                gpus.append({
                    "name": gpu.Name,
                    "driver_version": gpu.DriverVersion,
                    "adapter_ram": gpu.AdapterRAM,
                    "status": gpu.Status,
                })
        except Exception:
            pass
    return gpus


def get_temperature_info():
    temps = {"cpu": None, "gpu": None, "battery": None, "disks": []}
    try:
        sensor_temps = psutil.sensors_temperatures()
        if sensor_temps:
            for name, entries in sensor_temps.items():
                if entries:
                    current = entries[0].current
                    if "core" in name.lower() or "cpu" in name.lower():
                        temps["cpu"] = {"current": round(current, 1), "high": entries[0].high, "critical": entries[0].critical}
                    elif "gpu" in name.lower():
                        temps["gpu"] = {"current": round(current, 1), "high": entries[0].high, "critical": entries[0].critical}
                    else:
                        temps["disks"].append({"name": name, "current": round(current, 1)})
    except (AttributeError, Exception):
        pass
    if WMI_AVAILABLE and wmi_conn:
        try:
            for temp in wmi_conn.Win32_TemperatureProbe():
                if temp.CurrentReading:
                    temps.setdefault("wmi_temps", []).append({
                        "name": temp.Name,
                        "reading": temp.CurrentReading,
                    })
        except Exception:
            pass
    return temps


def get_fan_info():
    fans = []
    try:
        sensor_fans = psutil.sensors_fans()
        if sensor_fans:
            for name, entries in sensor_fans.items():
                for entry in entries:
                    fans.append({
                        "name": name,
                        "label": entry.label or name,
                        "current": entry.current,
                    })
    except (AttributeError, Exception):
        pass
    if WMI_AVAILABLE and wmi_conn:
        try:
            for fan in wmi_conn.Win32_Fan():
                fans.append({
                    "name": fan.Name,
                    "desired_speed": fan.DesiredSpeed,
                    "status": fan.Status,
                })
        except Exception:
            pass
    return fans


def get_battery_info():
    battery = psutil.sensors_battery()
    if battery is None:
        return None
    return {
        "percent": battery.percent,
        "power_plugged": battery.power_plugged,
        "secs_left": battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else -1,
        "time_left": str(datetime.timedelta(seconds=battery.secsleft)) if battery.secsleft > 0 and battery.secsleft != psutil.POWER_TIME_UNLIMITED else "Charging" if battery.power_plugged else "Unknown",
    }


def get_processes():
    procs = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info", "status", "username", "create_time"]):
        try:
            pinfo = proc.info
            procs.append({
                "pid": pinfo["pid"],
                "name": pinfo["name"],
                "cpu_percent": round(pinfo["cpu_percent"] or 0, 1),
                "memory_percent": round(pinfo["memory_percent"] or 0, 1),
                "memory_rss": pinfo["memory_info"].rss if pinfo["memory_info"] else 0,
                "status": pinfo["status"],
                "username": pinfo["username"],
                "create_time": datetime.fromtimestamp(pinfo["create_time"]).isoformat() if pinfo["create_time"] else None,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return procs[:100]


def get_system_info():
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot_time
    uname = platform.uname()
    return {
        "system": uname.system,
        "node_name": uname.node,
        "release": uname.release,
        "version": uname.version,
        "machine": uname.machine,
        "processor": uname.processor,
        "boot_time": boot_time.isoformat(),
        "uptime_seconds": int(uptime.total_seconds()),
        "uptime_human": str(uptime).split(".")[0],
        "hostname": socket.gethostname(),
        "ip_address": socket.gethostbyname(socket.gethostname()),
    }


def get_speedtest():
    now = time.time()
    if speedtest_cache["result"] and (now - speedtest_cache["timestamp"]) < 300:
        return speedtest_cache["result"]
    try:
        result = subprocess.run(
            ["python", "-m", "speedtest", "--simple"],
            capture_output=True, text=True, timeout=30
        )
        lines = result.stdout.strip().split("\n")
        parsed = {}
        for line in lines:
            if "Download" in line:
                parsed["download"] = line.split(":")[1].strip()
            elif "Upload" in line:
                parsed["upload"] = line.split(":")[1].strip()
            elif "Ping" in line:
                parsed["ping"] = line.split(":")[1].strip()
        speedtest_cache["result"] = parsed
        speedtest_cache["timestamp"] = now
        return parsed
    except Exception:
        return {"error": "Speedtest unavailable"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/system")
def api_system():
    return jsonify(get_system_info())


@app.route("/api/cpu")
def api_cpu():
    return jsonify(get_cpu_info())


@app.route("/api/memory")
def api_memory():
    return jsonify(get_memory_info())


@app.route("/api/disk")
def api_disk():
    return jsonify(get_disk_info())


@app.route("/api/network")
def api_network():
    return jsonify(get_network_info())


@app.route("/api/gpu")
def api_gpu():
    return jsonify(get_gpu_info())


@app.route("/api/temperatures")
def api_temperatures():
    return jsonify(get_temperature_info())


@app.route("/api/fans")
def api_fans():
    return jsonify(get_fan_info())


@app.route("/api/battery")
def api_battery():
    return jsonify(get_battery_info())


@app.route("/api/processes")
def api_processes():
    return jsonify(get_processes())


@app.route("/api/speedtest")
def api_speedtest():
    return jsonify(get_speedtest())


@app.route("/api/all")
def api_all():
    return jsonify({
        "system": get_system_info(),
        "cpu": get_cpu_info(),
        "memory": get_memory_info(),
        "disk": get_disk_info(),
        "network": get_network_info(),
        "gpu": get_gpu_info(),
        "temperatures": get_temperature_info(),
        "fans": get_fan_info(),
        "battery": get_battery_info(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5555, debug=False)
