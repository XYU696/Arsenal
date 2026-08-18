"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { SkillsResponse } from "@/lib/types";
import { Hero, Panel, StatusPill } from "@/components/ui";

export function SkillsPanel() {
  const [data, setData] = useState<SkillsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const result = await apiFetch<SkillsResponse>("/skills");
      setData(result);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function reload() {
    setLoading(true);
    try {
      const result = await apiFetch<SkillsResponse>("/skills/reload", { method: "POST" });
      setData(result);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "热加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="page">
      <Hero
        title="Skills 管理面板"
        description="EchoMind 会把技能说明动态注入到 Agent 的 system prompt。这个页面适合检查哪些规则已经被加载，以及热加载是否生效。"
        meta={
          <>
            <StatusPill tone="ok">skills {data?.count ?? 0}</StatusPill>
            <StatusPill>{loading ? "同步中" : "已连接"}</StatusPill>
          </>
        }
      />

      <Panel
        title="当前 Skills"
        subtitle={data ? `读取目录：${data.root_dir}` : "正在加载..."}
        action={
          <div className="button-row">
            <button className="ghost-button" onClick={() => void load()} disabled={loading}>
              刷新
            </button>
            <button className="button" onClick={() => void reload()} disabled={loading}>
              热加载
            </button>
          </div>
        }
      >
        {error ? <div className="status-pill error" style={{ marginBottom: "1rem" }}>{error}</div> : null}

        <div className="skill-list">
          {data?.skills.length ? (
            data.skills.map((skill) => (
              <article className="skill-item" key={skill.path}>
                <div className="split">
                  <h4>{skill.name}</h4>
                  <StatusPill tone={skill.enabled ? "ok" : "warn"}>
                    {skill.enabled ? "enabled" : "disabled"}
                  </StatusPill>
                </div>
                <p>{skill.description || "暂无描述。"}</p>
                <div className="message-meta">
                  <span>agents: {skill.agents.length ? skill.agents.join(", ") : "all"}</span>
                  <span>keywords: {skill.keywords.length ? skill.keywords.join(", ") : "all"}</span>
                  <span>chars: {skill.content_chars}</span>
                </div>
                <div className="mono muted" style={{ marginTop: "0.8rem" }}>{skill.path}</div>
              </article>
            ))
          ) : (
            <div className="empty">还没有加载到 Skill。</div>
          )}
        </div>

        {data?.errors.length ? (
          <div className="result-list" style={{ marginTop: "1rem" }}>
            {data.errors.map((item, index) => (
              <article className="result-item" key={`${item}-${index}`}>
                <h4>解析错误</h4>
                <p>{item}</p>
              </article>
            ))}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
