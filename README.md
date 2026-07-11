# System Health Monitor

A tiny React-based web app for monitoring local system health.

## Features

- React dashboard UI
- Live polling every 2.5 seconds
- CPU usage and load average
- Memory and swap usage
- Root disk usage
- Network throughput
- Uptime, process count, temperature if available
- Alert panel for high CPU, memory, disk, and temperature
- Affected CVE panel for detected running/package versions using NVD keyword search
- Zero backend dependencies: Python standard library only

## Run

```bash
cd system-health-monitor
python3 server.py
```

Open:

```text
http://127.0.0.1:8080
```

Optional environment variables:

```bash
HEALTH_HOST=127.0.0.1 HEALTH_PORT=3000 python3 server.py
```

## CVE scanning

The dashboard adds `/api/cves`, which detects common installed/running versions and checks the NVD public API with keyword searches.

Important: this is a quick visibility feature, not a full vulnerability scanner. Treat matches as "possibly affected" and confirm package/vendor applicability before acting.

Useful settings:

```bash
HEALTH_CVE_CACHE_TTL=1800 python3 server.py
```

## Notes

- The backend reads Linux system metrics from `/proc` and `/sys` when available.
- CVE scanning uses NVD over the internet and is cached to avoid hammering the public API.
- The frontend uses React from a CDN, so the browser needs internet access on first load.
- If you want a production Vite/Node version later, the UI can be moved into a normal React build easily.
