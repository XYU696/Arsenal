"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { HealthResponse, MonitorResponse } from "@/lib/types";
import { Hero, Panel, StatusPill } from "@/components/ui";

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

export function MonitorCenter() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [monitor, setMonitor] = useState<MonitorResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let closed = false;

    async function load() {
      try {
        const [healthData, monitorData] = await Promise.all([
          apiFetch<HealthResponse>("/health"),
          apiFetch<MonitorResponse>("/monitor")
        ]);
        if (!closed) {
          setHealth(healthData);
          setMonitor(monitorData);
          setError("");
        }
      } catch (err) {
        if (!closed) {
          setError(err instanceof Error ? err.message : "加载监控失败");
        }
      }
    }

    void load();
    const timer = window.setInterval(load, 8000);
    return () => {
      closed = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="page">
      <Hero
        title="在线监控中心"
        description="这里读取 `/monitor` 和 `/health`，把 Agent 成功率、平均耗时、工具状态、告警和建议放在一起，适合边测试边观察系统反馈。"
        meta={
          <>
            <StatusPill tone={error ? "error" : "ok"}>{error ? "监控异常" : "自动刷新中"}</StatusPill>
            <StatusPill>{monitor?.active_alerts.length ?? 0} active alerts</StatusPill>
          </>
        }
      />

      {error ? <div className="status-pill error">{error}</div> : null}

      <div className="grid three">
        {health &&
          Object.entries(health.agents).map(([name, stats]) => (
            <Panel key={name} title={name} subtitle="Agent 运行态">
              <div className="stack">
                <div className="metric">
                  <label>请求总数</label>
                  <strong>{stats.total}</strong>
                </div>
                <div className="split">
                  <span className="muted">成功率</span>
                  <strong>{percent(stats.success_rate)}</strong>
                </div>
                <div className="progress">
                  <span style={{ width: `${Math.max(6, stats.success_rate * 100)}%` }} />
                </div>
                <div className="split">
                  <span className="muted">平均耗时</span>
                  <strong>{stats.avg_ms.toFixed(1)} ms</strong>
                </div>
                <div className="split">
                  <span className="muted">路由评分</span>
                  <strong>{stats.routing_score.toFixed(3)}</strong>
                </div>
              </div>
            </Panel>
          ))}
      </div>

      <div className="grid two">
        <Panel title="工具状态" subtitle="来自 MCPToolManager 的运行统计。">
          <div className="stack">
            {monitor && Object.keys(monitor.tool_stats).length ? (
              Object.entries(monitor.tool_stats).map(([name, stats]) => (
                <div className="detail-item" key={name}>
                  <div className="split">
                    <strong>{name}</strong>
                    <StatusPill tone={stats.consecutive_fails > 0 ? "warn" : "ok"}>
                      {stats.circuit_state}
                    </StatusPill>
                  </div>
                  <div className="muted">成功率 {percent(stats.success_rate)}，平均耗时 {stats.avg_latency_ms.toFixed(1)} ms</div>
                </div>
              ))
            ) : (
              <div className="empty">暂无工具统计。</div>
            )}
          </div>
        </Panel>

        <Panel title="告警与建议" subtitle="最近的异常会优先显示在上方。">
          <div className="stack">
            <div className="alert-list">
              {monitor?.active_alerts.length ? (
                monitor.active_alerts.map((alert, index) => (
                  <article className="alert-item" key={`${alert.metric}-${index}`}>
                    <div className="split">
                      <h4>{alert.metric}</h4>
                      <StatusPill tone={alert.severity === "error" || alert.severity === "critical" ? "error" : "warn"}>
                        {alert.severity}
                      </StatusPill>
                    </div>
                    <p>{alert.message}</p>
                  </article>
                ))
              ) : (
                <div className="empty">当前没有活跃告警。</div>
              )}
            </div>

            <div className="result-list">
              {monitor?.suggestions.length ? (
                monitor.suggestions.map((item, index) => (
                  <article className="result-item" key={`${item.title}-${index}`}>
                    <div className="split">
                      <h4>{item.title}</h4>
                      <StatusPill>Priority {item.priority}</StatusPill>
                    </div>
                    <p>{item.action}</p>
                  </article>
                ))
              ) : (
                <div className="empty">还没有系统建议，说明当前没有明显异常模式。</div>
              )}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
