const { createElement: h, useCallback, useEffect, useMemo, useState } = React;
const MAX_POINTS = 40;

function formatBytesPerSecond(value) {
  if (value === null || value === undefined) return "Unavailable";
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let n = Number(value);
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatUptime(seconds) {
  if (seconds === null || seconds === undefined) return "Unavailable";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h ${minutes}m`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function valueOrDash(value, suffix = "") {
  if (value === null || value === undefined) return "—";
  return `${value}${suffix}`;
}

function worstStatus(metrics) {
  if (!metrics) return "unknown";
  if (metrics.alerts?.some((alert) => alert.level === "critical")) return "critical";
  if (metrics.alerts?.length) return "warning";
  const statuses = [metrics.cpu?.status, metrics.memory?.status, metrics.disk?.status];
  if (statuses.includes("critical")) return "critical";
  if (statuses.includes("warning")) return "warning";
  if (statuses.every(Boolean)) return "healthy";
  return "unknown";
}

function accentFor(status) {
  if (status === "critical") return "#ff6b6b";
  if (status === "warning") return "#ffd166";
  if (status === "healthy") return "#2ee59d";
  return "#58a6ff";
}

function MetricCard({ title, value, suffix, sub, percent, status = "unknown" }) {
  const safePercent = Math.max(0, Math.min(Number(percent || 0), 100));
  return h(
    "section",
    { className: "card", style: { "--accent": accentFor(status) } },
    h("div", { className: "card-top" },
      h("p", { className: "card-title" }, title),
      h("span", { className: `badge ${status}` }, status)
    ),
    h("p", { className: "metric-value" }, valueOrDash(value, suffix)),
    h("p", { className: "metric-sub" }, sub),
    percent !== undefined && h("div", { className: "progress" }, h("span", { style: { "--value": `${safePercent}%` } }))
  );
}

function MetaCard({ label, value }) {
  return h("section", { className: "meta-card" },
    h("p", { className: "meta-label" }, label),
    h("p", { className: "meta-value" }, value || "—")
  );
}

function MiniChart({ history }) {
  const width = 900;
  const height = 280;
  const pad = 24;
  const series = [
    { key: "cpu", label: "CPU", color: "#58a6ff" },
    { key: "memory", label: "Memory", color: "#2ee59d" },
    { key: "disk", label: "Disk", color: "#b794f4" },
  ];
  const lines = series.map((item) => {
    const points = history.map((point, index) => {
      const x = pad + (index / Math.max(history.length - 1, 1)) * (width - pad * 2);
      const y = height - pad - (Number(point[item.key] || 0) / 100) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    return h("polyline", {
      key: item.key,
      points,
      fill: "none",
      stroke: item.color,
      strokeWidth: 4,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    });
  });

  return h(React.Fragment, null,
    h("div", { className: "chart" },
      h("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height: "100%", preserveAspectRatio: "none" },
        [0, 25, 50, 75, 100].map((tick) => {
          const y = height - pad - (tick / 100) * (height - pad * 2);
          return h("g", { key: tick },
            h("line", { x1: pad, x2: width - pad, y1: y, y2: y, stroke: "rgba(148, 163, 184, 0.16)", strokeWidth: 1 }),
            h("text", { x: 8, y: y + 4, fill: "rgba(237, 243, 255, 0.48)", fontSize: 16 }, tick)
          );
        }),
        lines
      )
    ),
    h("div", { className: "legend" },
      series.map((item) => h("span", { className: "legend-item", key: item.key },
        h("span", { className: "legend-line", style: { background: item.color } }),
        item.label
      ))
    )
  );
}

function Alerts({ alerts }) {
  const list = alerts?.length ? alerts : [{ level: "healthy", message: "No active alerts. System looks good." }];
  return h("section", { className: "alerts" },
    h("div", { className: "panel-title" }, h("h2", null, "Alerts"), h("span", null, `${alerts?.length || 0} active`)),
    h("ul", { className: "alert-list" },
      list.map((alert, index) => h("li", { className: `alert ${alert.level}`, key: index }, alert.message))
    )
  );
}

function HostPanel({ metrics }) {
  const rows = [
    ["Hostname", metrics?.host?.hostname],
    ["Platform", metrics?.host?.platform],
    ["Machine", metrics?.host?.machine],
    ["CPU cores", metrics?.cpu?.cores],
    ["Load avg", metrics?.cpu?.load ? `${metrics.cpu.load.one} / ${metrics.cpu.load.five} / ${metrics.cpu.load.fifteen}` : null],
    ["Updated", metrics ? new Date(metrics.timestamp * 1000).toLocaleTimeString() : null],
  ];
  return h("section", { className: "host-panel" },
    h("div", { className: "panel-title" }, h("h2", null, "Host details"), h("span", null, "live")),
    h("div", { className: "host-table" },
      rows.map(([label, value]) => h("div", { className: "host-row", key: label },
        h("span", null, label),
        h("strong", null, value ?? "—")
      ))
    )
  );
}

function severityClass(severity) {
  const value = String(severity || "unknown").toLowerCase();
  if (["critical", "high", "medium", "low"].includes(value)) return value;
  return "unknown";
}

function CvePanel({ cveData, cveLoading, cveError, onRefresh }) {
  const affected = cveData?.affected || [];
  const running = cveData?.running_versions || [];
  const updated = cveData?.timestamp ? new Date(cveData.timestamp * 1000).toLocaleTimeString() : "never";

  return h("section", { className: "cve-panel" },
    h("div", { className: "panel-title" },
      h("div", null,
        h("h2", null, "Affected CVEs on running versions"),
        h("p", { className: "panel-note" }, cveData?.note || "Scans detected running/package versions against NVD keyword search.")
      ),
      h("button", { className: "refresh small", onClick: () => onRefresh(true), disabled: cveLoading }, cveLoading ? "Scanning…" : "Rescan CVEs")
    ),

    cveError && h("div", { className: "error compact" }, `CVE scan error: ${cveError}`),

    h("div", { className: "cve-summary" },
      h("div", null, h("strong", null, affected.length), h("span", null, " affected products")),
      h("div", null, h("strong", null, running.length), h("span", null, " versions detected")),
      h("div", null, h("strong", null, updated), h("span", null, cveData?.cached ? " cached" : " updated"))
    ),

    affected.length === 0 && !cveLoading && h("div", { className: "empty-state" },
      cveData ? "No matching CVEs found for detected versions." : "CVE scan has not completed yet."
    ),

    affected.length > 0 && h("div", { className: "cve-list" },
      affected.map((product) => h("article", { className: "cve-product", key: `${product.name}-${product.version}` },
        h("div", { className: "cve-product-head" },
          h("div", null,
            h("h3", null, product.name),
            h("p", null, `Version ${product.version} · ${product.source} · lookup: ${product.lookup}`)
          ),
          h("span", { className: "cve-count" }, `${product.cve_count} matches`)
        ),
        h("div", { className: "cve-items" },
          (product.cves || []).map((cve) => h("a", { className: "cve-item", href: cve.url, target: "_blank", rel: "noreferrer", key: cve.id },
            h("div", { className: "cve-row" },
              h("strong", null, cve.id),
              h("span", { className: `severity ${severityClass(cve.severity)}` }, `${cve.severity || "unknown"}${cve.score ? ` ${cve.score}` : ""}`)
            ),
            h("p", null, cve.summary || "No summary available."),
            h("small", null, cve.published ? `Published ${cve.published.slice(0, 10)}` : "Publication date unknown")
          ))
        )
      ))
    ),

    running.length > 0 && h("details", { className: "running-versions" },
      h("summary", null, "Detected running/package versions"),
      h("div", { className: "version-grid" },
        running.map((item) => h("div", { className: "version-pill", key: `${item.name}-${item.version}-${item.source}` },
          h("strong", null, item.name),
          h("span", null, item.version),
          h("small", null, item.source)
        ))
      )
    )
  );
}

function App() {
  const [metrics, setMetrics] = useState(null);
  const [history, setHistory] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [cveData, setCveData] = useState(null);
  const [cveError, setCveError] = useState("");
  const [cveLoading, setCveLoading] = useState(false);

  const fetchMetrics = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/metrics", { cache: "no-store" });
      if (!response.ok) throw new Error(`Backend returned ${response.status}`);
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      setMetrics(data);
      setHistory((previous) => {
        const next = previous.concat({
          t: data.timestamp,
          cpu: data.cpu?.used_percent || 0,
          memory: data.memory?.used_percent || 0,
          disk: data.disk?.used_percent || 0,
        });
        return next.slice(-MAX_POINTS);
      });
      setError("");
    } catch (err) {
      setError(err.message || "Unable to fetch metrics");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCves = useCallback(async (refresh = false) => {
    setCveLoading(true);
    try {
      const response = await fetch(`/api/cves${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Backend returned ${response.status}`);
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      setCveData(data);
      setCveError("");
    } catch (err) {
      setCveError(err.message || "Unable to fetch CVE list");
    } finally {
      setCveLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMetrics();
    fetchCves(false);
    const id = setInterval(fetchMetrics, 2500);
    const cveId = setInterval(() => fetchCves(false), 30 * 60 * 1000);
    return () => {
      clearInterval(id);
      clearInterval(cveId);
    };
  }, [fetchMetrics, fetchCves]);

  const overall = useMemo(() => worstStatus(metrics), [metrics]);

  if (!metrics && !error) {
    return h("main", { className: "loading" }, "Starting dashboard…");
  }

  return h("main", { className: "app" },
    h("header", { className: "header" },
      h("div", null,
        h("p", { className: "eyebrow" }, "React system dashboard"),
        h("h1", null, "System Health Monitor"),
        h("p", { className: "subtitle" }, "Live CPU, memory, disk, network, uptime, temperature, and alert monitoring from a tiny Python backend.")
      ),
      h("div", { className: "header-actions" },
        h("div", { className: "status-pill" }, h("span", { className: `dot ${overall}` }), `Overall: ${overall}`),
        h("button", { className: "refresh", onClick: fetchMetrics, disabled: loading }, loading ? "Refreshing…" : "Refresh now")
      )
    ),

    error && h("div", { className: "error" }, `Backend error: ${error}`),

    h("section", { className: "meta-grid" },
      h(MetaCard, { label: "Uptime", value: formatUptime(metrics?.uptime_seconds) }),
      h(MetaCard, { label: "Temperature", value: metrics?.temperature_c == null ? "Unavailable" : `${metrics.temperature_c}°C` }),
      h(MetaCard, { label: "Processes", value: metrics?.processes ?? "Unavailable" }),
      h(MetaCard, { label: "Network", value: `${formatBytesPerSecond(metrics?.network?.rx_per_sec)} down / ${formatBytesPerSecond(metrics?.network?.tx_per_sec)} up` })
    ),

    h("section", { className: "metrics-grid" },
      h(MetricCard, {
        title: "CPU usage",
        value: metrics?.cpu?.used_percent,
        suffix: "%",
        percent: metrics?.cpu?.used_percent,
        status: metrics?.cpu?.status,
        sub: `Load ${metrics?.cpu?.load?.one ?? "—"} · ${metrics?.cpu?.cores ?? "—"} cores`,
      }),
      h(MetricCard, {
        title: "Memory",
        value: metrics?.memory?.used_percent,
        suffix: "%",
        percent: metrics?.memory?.used_percent,
        status: metrics?.memory?.status,
        sub: `${metrics?.memory?.used_gb ?? "—"} GB used / ${metrics?.memory?.total_gb ?? "—"} GB`,
      }),
      h(MetricCard, {
        title: "Disk /",
        value: metrics?.disk?.used_percent,
        suffix: "%",
        percent: metrics?.disk?.used_percent,
        status: metrics?.disk?.status,
        sub: `${metrics?.disk?.free_gb ?? "—"} GB free / ${metrics?.disk?.total_gb ?? "—"} GB`,
      }),
      h(MetricCard, {
        title: "Swap",
        value: metrics?.memory?.swap_used_percent,
        suffix: "%",
        percent: metrics?.memory?.swap_used_percent,
        status: metrics?.memory?.swap_used_percent >= 70 ? "warning" : "healthy",
        sub: `${metrics?.memory?.swap_used_gb ?? "—"} GB used / ${metrics?.memory?.swap_total_gb ?? "—"} GB`,
      })
    ),

    h("section", { className: "lower-grid" },
      h("section", { className: "chart-panel" },
        h("div", { className: "panel-title" }, h("h2", null, "Usage trend"), h("span", null, `${history.length} samples`)),
        h(MiniChart, { history })
      ),
      h("div", { className: "side-stack" },
        h(Alerts, { alerts: metrics?.alerts }),
        h(HostPanel, { metrics })
      )
    ),

    h(CvePanel, { cveData, cveLoading, cveError, onRefresh: fetchCves })
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
