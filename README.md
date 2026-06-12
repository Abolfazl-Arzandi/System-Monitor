# 🖥️ System Monitor

A local web-based system monitor built with Python and Flask. Exposes real-time hardware and OS metrics through a REST API served on `localhost:5555`.

---

## How It Was Built

### Architecture

The project is a minimal Flask application with two layers:

- **`app.py`** — backend server; collects system data and exposes it as JSON endpoints
- **`templates/index.html`** — frontend dashboard that consumes those endpoints (fetched in-browser)

Flask-CORS is enabled so the frontend can freely call the API even when served from a different origin during development.

### Data Collection

All hardware data is collected through **`psutil`**, a cross-platform library for system and process information. Each resource type has its own collector function:

| Function | What it collects |
|---|---|
| `get_cpu_info()` | Usage %, per-core usage, frequency, time breakdown (user/system/idle/interrupt) |
| `get_memory_info()` | RAM total/used/available, cache, buffers, swap |
| `get_disk_info()` | All mounted partitions with usage, plus cumulative I/O counters |
| `get_network_info()` | All interfaces with addresses and link status, plus total I/O counters |
| `get_temperature_info()` | CPU/GPU/disk temperatures from `psutil.sensors_temperatures()` |
| `get_fan_info()` | Fan speeds from `psutil.sensors_fans()` |
| `get_battery_info()` | Charge percent, plugged status, estimated time remaining |
| `get_processes()` | Top 100 processes sorted by CPU usage |
| `get_system_info()` | OS details, hostname, IP, boot time, uptime |

### Optional Dependencies

The app degrades gracefully when optional packages are missing:

- **`py-cpuinfo`** — adds CPU brand name, architecture, and advertised clock speed to the CPU response. Falls back silently if not installed.
- **`GPUtil`** — reads NVIDIA GPU load and VRAM usage. Falls back to the WMI path on Windows, or returns an empty list.
- **`WMI`** (Windows only) — supplements GPU and temperature data using Windows Management Instrumentation when the above libraries can't provide it. Skipped entirely on Linux/macOS.

### Speedtest

`/api/speedtest` runs `speedtest-cli` as a subprocess and parses its output for download speed, upload speed, and ping. Results are **cached for 5 minutes** in a simple in-memory dict to avoid re-running a costly network test on every request.

### API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Serves the frontend dashboard |
| `GET /api/system` | OS and host information |
| `GET /api/cpu` | CPU usage and frequency |
| `GET /api/memory` | RAM and swap |
| `GET /api/disk` | Disk partitions and I/O |
| `GET /api/network` | Network interfaces and I/O |
| `GET /api/gpu` | GPU usage and VRAM |
| `GET /api/temperatures` | Sensor temperatures |
| `GET /api/fans` | Fan speeds |
| `GET /api/battery` | Battery status |
| `GET /api/processes` | Top 100 processes by CPU |
| `GET /api/speedtest` | Internet speed (cached 5 min) |
| `GET /api/all` | All of the above in one response |

---

## How to Run

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `WMI` is Windows-only. On Linux/macOS, remove it from `requirements.txt` before installing — it will fail to build on non-Windows platforms.

### 2. Start the server

```bash
python app.py
```

The server starts on `http://127.0.0.1:5555`.

### 3. Open the dashboard

Navigate to `http://localhost:5555` in your browser.

---

## Notes

- **Temperatures and fan sensors** are only available on Linux and some macOS setups. On Windows, data comes from WMI which may require running as Administrator.
- **GPU data** requires an NVIDIA card for full load/VRAM metrics via GPUtil. Other vendors may only show basic info via WMI.
- **Speedtest** requires `speedtest-cli` to be reachable as a Python module (`python -m speedtest`). Results are cached to avoid slow repeated calls.
- The server binds to `127.0.0.1` only — it is not exposed on the local network by default.
