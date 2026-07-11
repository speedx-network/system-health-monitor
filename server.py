#!/usr/bin/env python3
"""Tiny backend for the React system health dashboard."""

from __future__ import annotations

import ctypes
import json
import mimetypes
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = os.environ.get("HEALTH_HOST", "0.0.0.0")
PORT = int(os.environ.get("HEALTH_PORT", "8080"))
PROC_ROOT = Path(os.environ.get("HEALTH_PROC_ROOT", "/proc"))
SYS_ROOT = Path(os.environ.get("HEALTH_SYS_ROOT", "/sys"))

_last_cpu = None
_last_net = None


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def bytes_to_gb(value: int | float) -> float:
    return round(float(value) / (1024**3), 2)


def percent(used: int | float, total: int | float) -> float:
    if not total:
        return 0.0
    return round((float(used) / float(total)) * 100, 1)


def is_windows() -> bool:
    return platform.system().lower() == "windows"


class WindowsFileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

    def as_int(self) -> int:
        return (int(self.dwHighDateTime) << 32) | int(self.dwLowDateTime)


def _windows_kernel32():
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetSystemTimes.argtypes = [
            ctypes.POINTER(WindowsFileTime),
            ctypes.POINTER(WindowsFileTime),
            ctypes.POINTER(WindowsFileTime),
        ]
        kernel32.GetSystemTimes.restype = ctypes.c_int
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return kernel32
    except (AttributeError, OSError):
        return None


def cpu_snapshot() -> tuple[int, int] | None:
    if is_windows():
        kernel32 = _windows_kernel32()
        if kernel32 is None:
            return None
        idle = WindowsFileTime()
        kernel = WindowsFileTime()
        user = WindowsFileTime()
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        idle_value = idle.as_int()
        total = kernel.as_int() + user.as_int()
        return idle_value, total

    data = read_text(PROC_ROOT / "stat")
    if not data:
        return None
    first = data.splitlines()[0].split()
    if not first or first[0] != "cpu":
        return None
    values = [int(v) for v in first[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return idle, total


def cpu_percent() -> float | None:
    global _last_cpu
    current = cpu_snapshot()
    if current is None:
        return None
    if _last_cpu is None:
        _last_cpu = current
        return 0.0
    idle_delta = current[0] - _last_cpu[0]
    total_delta = current[1] - _last_cpu[1]
    _last_cpu = current
    if total_delta <= 0:
        return 0.0
    return round(100.0 * (1.0 - idle_delta / total_delta), 1)


class WindowsMemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def windows_memory_metrics() -> dict:
    kernel32 = _windows_kernel32()
    if kernel32 is None:
        return {"available": False, "used_percent": None}
    status = WindowsMemoryStatus()
    status.dwLength = ctypes.sizeof(WindowsMemoryStatus)
    try:
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(WindowsMemoryStatus)]
        kernel32.GlobalMemoryStatusEx.restype = ctypes.c_int
    except AttributeError:
        return {"available": False, "used_percent": None}
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"available": False, "used_percent": None}

    total = int(status.ullTotalPhys)
    available = int(status.ullAvailPhys)
    used = max(total - available, 0)
    page_total = int(status.ullTotalPageFile)
    page_available = int(status.ullAvailPageFile)
    page_used = max(page_total - page_available, 0)

    return {
        "available": True,
        "total_gb": bytes_to_gb(total),
        "used_gb": bytes_to_gb(used),
        "free_gb": bytes_to_gb(available),
        "used_percent": percent(used, total),
        "swap_total_gb": bytes_to_gb(page_total),
        "swap_used_gb": bytes_to_gb(page_used),
        "swap_used_percent": percent(page_used, page_total) if page_total else 0.0,
    }


def memory_metrics() -> dict:
    if is_windows():
        return windows_memory_metrics()

    data = read_text(PROC_ROOT / "meminfo")
    if not data:
        return {"available": False, "used_percent": None}

    values = {}
    for line in data.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if parts:
            values[key] = int(parts[0]) * 1024

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(total - available, 0)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)

    return {
        "available": True,
        "total_gb": bytes_to_gb(total),
        "used_gb": bytes_to_gb(used),
        "free_gb": bytes_to_gb(available),
        "used_percent": percent(used, total),
        "swap_total_gb": bytes_to_gb(swap_total),
        "swap_used_gb": bytes_to_gb(swap_used),
        "swap_used_percent": percent(swap_used, swap_total) if swap_total else 0.0,
    }


def disk_metrics() -> dict:
    path = os.environ.get("HEALTH_DISK_PATH") or (os.environ.get("SystemDrive", "C:") + "\\" if is_windows() else "/")
    usage = shutil.disk_usage(path)
    return {
        "path": path,
        "total_gb": bytes_to_gb(usage.total),
        "used_gb": bytes_to_gb(usage.used),
        "free_gb": bytes_to_gb(usage.free),
        "used_percent": percent(usage.used, usage.total),
    }


def windows_network_totals() -> tuple[int, int] | None:
    output = run_command(["netstat", "-e"], timeout=6)
    if not output:
        return None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].lower().startswith("bytes"):
            try:
                return int(parts[1]), int(parts[2])
            except ValueError:
                return None
    return None


def network_snapshot() -> tuple[float, int, int] | None:
    if is_windows():
        totals = windows_network_totals()
        if totals is None:
            return None
        return time.time(), totals[0], totals[1]

    data = read_text(PROC_ROOT / "net" / "dev")
    if not data:
        return None
    rx = 0
    tx = 0
    for line in data.splitlines()[2:]:
        if ":" not in line:
            continue
        iface, raw = line.split(":", 1)
        iface = iface.strip()
        if iface == "lo":
            continue
        parts = raw.split()
        if len(parts) >= 16:
            rx += int(parts[0])
            tx += int(parts[8])
    return time.time(), rx, tx


def network_metrics() -> dict:
    global _last_net
    current = network_snapshot()
    if current is None:
        return {"available": False, "rx_per_sec": None, "tx_per_sec": None}
    if _last_net is None:
        _last_net = current
        return {"available": True, "rx_per_sec": 0, "tx_per_sec": 0, "rx_total": current[1], "tx_total": current[2]}

    elapsed = max(current[0] - _last_net[0], 0.001)
    rx_rate = max((current[1] - _last_net[1]) / elapsed, 0)
    tx_rate = max((current[2] - _last_net[2]) / elapsed, 0)
    _last_net = current

    return {
        "available": True,
        "rx_per_sec": round(rx_rate, 1),
        "tx_per_sec": round(tx_rate, 1),
        "rx_total": current[1],
        "tx_total": current[2],
    }


def uptime_seconds() -> float | None:
    if is_windows():
        kernel32 = _windows_kernel32()
        if kernel32 is None:
            return None
        try:
            return round(kernel32.GetTickCount64() / 1000, 0)
        except AttributeError:
            return None

    data = read_text(PROC_ROOT / "uptime")
    if not data:
        return None
    try:
        return round(float(data.split()[0]), 0)
    except (ValueError, IndexError):
        return None


def temperature_c() -> float | None:
    if is_windows():
        # Windows does not expose a stable built-in temperature API through the
        # standard library. Keep the dashboard alive and report this as unknown.
        return None

    thermal_root = SYS_ROOT / "class" / "thermal"
    try:
        zones = sorted(thermal_root.glob("thermal_zone*/temp"))
    except OSError:
        return None

    readings = []
    for zone in zones:
        try:
            raw = zone.read_text(encoding="utf-8").strip()
            value = float(raw)
            if value > 1000:
                value = value / 1000
            if -20 <= value <= 150:
                readings.append(value)
        except (OSError, ValueError):
            continue
    if not readings:
        return None
    return round(max(readings), 1)


def process_count() -> int | None:
    if is_windows():
        output = run_command(["tasklist", "/FO", "CSV", "/NH"], timeout=8)
        if output is None:
            return None
        return sum(1 for line in output.splitlines() if line.strip())

    try:
        return sum(1 for child in PROC_ROOT.iterdir() if child.name.isdigit())
    except OSError:
        return None


def load_average() -> dict:
    if not hasattr(os, "getloadavg"):
        return {"one": None, "five": None, "fifteen": None}
    try:
        one, five, fifteen = os.getloadavg()
        return {"one": round(one, 2), "five": round(five, 2), "fifteen": round(fifteen, 2)}
    except OSError:
        return {"one": None, "five": None, "fifteen": None}


def status_for(value: float | None, warn: float, critical: float) -> str:
    if value is None:
        return "unknown"
    if value >= critical:
        return "critical"
    if value >= warn:
        return "warning"
    return "healthy"


CVE_CACHE_TTL = int(os.environ.get("HEALTH_CVE_CACHE_TTL", "1800"))
CVE_MAX_PRODUCTS = int(os.environ.get("HEALTH_CVE_MAX_PRODUCTS", "5"))
CVE_REQUEST_TIMEOUT = int(os.environ.get("HEALTH_CVE_TIMEOUT", "8"))
NVD_API_KEY = os.environ.get("HEALTH_NVD_API_KEY") or os.environ.get("NVD_API_KEY")
NVD_DELAY = float(os.environ.get("HEALTH_NVD_DELAY", "0.65" if NVD_API_KEY else "6.1"))
_cve_cache: dict[str, object] = {"timestamp": 0, "query": None, "data": None}


def run_command(command: list[str], timeout: int = 8) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0 and not output:
        return None
    return output


def normalize_product_name(name: str) -> str:
    cleaned = name.strip().lower()
    cleaned = cleaned.replace("python3", "python").replace("python-", "python")
    cleaned = re.sub(r"[^a-z0-9.+_-]+", "-", cleaned)
    return cleaned.strip("-")


def normalize_version(version: str) -> str:
    value = version.strip().splitlines()[0].strip()
    # Debian package versions may include an epoch prefix like "1:6.18.34-1".
    value = re.sub(r"^\d+:", "", value)
    value = value.split("-", 1)[0]
    value = value.split("+", 1)[0]
    match = re.search(r"\d+(?:\.\d+){0,3}", value)
    return match.group(0) if match else value


def detect_running_versions(limit: int = 24) -> list[dict]:
    """Return likely internet-facing running software versions."""
    candidates: dict[str, dict] = {}
    dpkg_status = Path(os.environ.get("HEALTH_DPKG_STATUS", "/var/lib/dpkg/status"))

    commands = [
        ("python", ["python", "--version"] if is_windows() else ["python3", "--version"]),
        ("openssl", ["openssl", "version"]),
        ("ssh", ["ssh", "-V"]),
        ("node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("nginx", ["nginx", "-v"]),
        ("apache", ["httpd", "-v"] if is_windows() else ["apache2", "-v"]),
        ("git", ["git", "--version"]),
        ("curl", ["curl", "--version"]),
    ]
    for name, command in commands:
        output = run_command(command)
        if not output:
            continue
        version = normalize_version(output)
        candidates[name] = {
            "name": name,
            "product": normalize_product_name(name),
            "version": version,
            "source": "command",
            "raw": output.splitlines()[0][:160],
        }

    if is_windows():
        return sorted(candidates.values(), key=lambda item: item["name"])[:limit]

    dpkg_data = read_text(dpkg_status)
    interesting = {
        "openssh-server", "openssh-client", "openssl", "python3", "nodejs", "npm",
        "nginx", "apache2", "curl", "git", "sudo", "systemd", "linux-image-arm64",
        "linux-image-amd64", "linux-image-rpi-v8", "linux-image-raspi",
    }
    if dpkg_data:
        for block in dpkg_data.split("\n\n"):
            fields = {}
            for line in block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key] = value.strip()
            package = fields.get("Package")
            version = fields.get("Version")
            status = fields.get("Status", "")
            if not package or not version or "installed" not in status:
                continue
            if package not in interesting and not any(token in package for token in ("kernel", "linux-image", "openssh", "openssl", "nginx", "apache")):
                continue
            key = normalize_product_name(package)
            candidates.setdefault(key, {
                "name": package,
                "product": key,
                "version": normalize_version(version),
                "source": "dpkg",
                "raw": version[:160],
            })

    return sorted(candidates.values(), key=lambda item: item["name"])[:limit]


def extract_cvss(metrics: dict) -> tuple[float | None, str]:
    cvss = metrics.get("cvssMetricV31") or metrics.get("cvssMetricV30") or metrics.get("cvssMetricV2") or []
    if not cvss:
        return None, "unknown"
    data = cvss[0].get("cvssData", {})
    score = data.get("baseScore")
    severity = cvss[0].get("baseSeverity") or data.get("baseSeverity") or "unknown"
    return score, str(severity).lower()


def fetch_cves_for_product(product: str, version: str, max_results: int = 5) -> dict:
    keyword = f"{product} {version}"
    query = urlencode({"keywordSearch": keyword, "resultsPerPage": max_results})
    url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?{query}"
    headers = {"User-Agent": "system-health-monitor/1.0"}
    if NVD_API_KEY:
        headers["apiKey"] = NVD_API_KEY
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=CVE_REQUEST_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8", errors="ignore"))

    items = []
    for entry in payload.get("vulnerabilities", []):
        cve = entry.get("cve", {})
        descriptions = cve.get("descriptions", [])
        summary = ""
        for description in descriptions:
            if description.get("lang") == "en":
                summary = description.get("value", "")
                break
        score, severity = extract_cvss(cve.get("metrics", {}))
        items.append({
            "id": cve.get("id"),
            "severity": severity,
            "score": score,
            "published": cve.get("published"),
            "lastModified": cve.get("lastModified"),
            "summary": summary[:320],
            "url": f"https://nvd.nist.gov/vuln/detail/{cve.get('id')}",
        })

    return {"keyword": keyword, "count": payload.get("totalResults", len(items)), "items": items}


def cve_scan(refresh: bool = False) -> dict:
    running = detect_running_versions()
    query = [(item["product"], item["version"]) for item in running]
    now = time.time()
    if not refresh and _cve_cache.get("data") and _cve_cache.get("query") == query and now - float(_cve_cache.get("timestamp", 0)) < CVE_CACHE_TTL:
        cached = dict(_cve_cache["data"])  # shallow copy is enough for response metadata
        cached["cached"] = True
        return cached

    products = []
    errors = []
    # Keep this intentionally small so a dashboard refresh does not hammer NVD.
    scan_targets = running[:CVE_MAX_PRODUCTS]
    for index, item in enumerate(scan_targets):
        product = {**item, "cves": [], "cve_count": 0, "lookup": f"{item['product']} {item['version']}"}
        try:
            result = fetch_cves_for_product(item["product"], item["version"])
            product["cves"] = result["items"]
            product["cve_count"] = result["count"]
        except Exception as exc:
            errors.append({"product": item["name"], "version": item["version"], "error": str(exc)})
        products.append(product)
        if index < len(scan_targets) - 1:
            time.sleep(NVD_DELAY)  # NVD public API rate friendliness

    affected = [product for product in products if product.get("cve_count", 0) > 0]
    data = {
        "timestamp": int(now),
        "cached": False,
        "source": "NVD keyword search",
        "note": "Keyword-based CVE matching for detected running/package versions. Confirm applicability before treating as exploitable.",
        "products_scanned": len(products),
        "affected_count": len(affected),
        "running_versions": running,
        "affected": affected,
        "errors": errors,
    }
    _cve_cache.update({"timestamp": now, "query": query, "data": data})
    return data


def collect_metrics() -> dict:
    cpu = cpu_percent()
    memory = memory_metrics()
    disk = disk_metrics()
    net = network_metrics()
    temp = temperature_c()

    alerts = []
    if cpu is not None and cpu >= 85:
        alerts.append({"level": "critical" if cpu >= 95 else "warning", "message": f"CPU usage is high: {cpu}%"})
    if memory.get("used_percent") is not None and memory["used_percent"] >= 85:
        alerts.append({"level": "critical" if memory["used_percent"] >= 95 else "warning", "message": f"Memory usage is high: {memory['used_percent']}%"})
    if disk.get("used_percent", 0) >= 85:
        alerts.append({"level": "critical" if disk["used_percent"] >= 95 else "warning", "message": f"Disk usage is high: {disk['used_percent']}%"})
    if temp is not None and temp >= 75:
        alerts.append({"level": "critical" if temp >= 85 else "warning", "message": f"Temperature is high: {temp}°C"})

    return {
        "timestamp": int(time.time()),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "cpu": {"used_percent": cpu, "status": status_for(cpu, 70, 90), "load": load_average(), "cores": os.cpu_count()},
        "memory": {**memory, "status": status_for(memory.get("used_percent"), 75, 90)},
        "disk": {**disk, "status": status_for(disk.get("used_percent"), 75, 90)},
        "network": net,
        "uptime_seconds": uptime_seconds(),
        "temperature_c": temp,
        "processes": process_count(),
        "alerts": alerts,
    }


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "SystemHealthMonitor/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/metrics":
            try:
                self.send_json(collect_metrics())
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/cves":
            try:
                params = parse_qs(parsed.query)
                refresh = params.get("refresh", ["0"])[0] in {"1", "true", "yes"}
                self.send_json(cve_scan(refresh=refresh))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=500)
            return

        if path == "/":
            path = "/index.html"

        requested = (STATIC / path.lstrip("/")).resolve()
        if not str(requested).startswith(str(STATIC.resolve())) or not requested.exists() or requested.is_dir():
            self.send_error(404, "Not found")
            return

        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        body = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), HealthHandler)
    print(f"System health monitor running at http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
