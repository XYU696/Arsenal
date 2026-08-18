"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import type { EvalResponse } from "@/lib/types";
import { Hero, Panel, StatusPill } from "@/components/ui";

export function EvalLab() {
  const [report, setReport] = useState<EvalResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const avgScoreValues = report ? Object.values(report.avg_scores) : [];
  const averageQualityScore = avgScoreValues.length
    ? avgScoreValues.reduce((sum, item) => sum + item, 0) / avgScoreValues.length
    : null;

  async function run() {
    setRunning(true);
    setError("");
    try {
      const result = await apiFetch<EvalResponse>("/eval/run", {
        method: "POST",
        body: JSON.stringify({})
      });
      setReport(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "评测失败");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page">
      <Hero
        title="评测实验室"
        description="这个页面直接触发后端的端到端评测接口。适合在你改动 Prompt、Skills 或知识库后，快速做一轮回归验证。"
        meta={
          <>
            <StatusPill tone={report && report.pass_rate >= 0.75 ? "ok" : "warn"}>
              pass rate {report ? `${(report.pass_rate * 100).toFixed(1)}%` : "--"}
            </StatusPill>
            <StatusPill>{running ? "评测运行中" : "待执行"}</StatusPill>
          </>
        }
      />

      <Panel
        title="运行评测"
        subtitle="默认调用后端内置测试集。首次运行可能需要一点时间，因为会触发真实的模型调用。"
        action={
          <button className="button" onClick={() => void run()} disabled={running}>
            {running ? "评测中..." : "开始评测"}
          </button>
        }
      >
        {error ? <div className="status-pill error">{error}</div> : null}

        {report ? (
          <div className="stack">
            <div className="grid three">
              <div className="metric">
                <label>通过率</label>
                <strong>{(report.pass_rate * 100).toFixed(1)}%</strong>
              </div>
              <div className="metric">
                <label>通过数</label>
                <strong>{report.passed} / {report.total}</strong>
              </div>
              <div className="metric">
                <label>平均质量分</label>
                <strong>{averageQualityScore !== null ? averageQualityScore.toFixed(3) : "--"}</strong>
              </div>
            </div>

            <div className="grid two">
              <div className="result-list">
                {Object.entries(report.avg_scores).map(([key, value]) => (
                  <article className="result-item" key={key}>
                    <div className="split">
                      <h4>{key}</h4>
                      <StatusPill>{value.toFixed(3)}</StatusPill>
                    </div>
                    <div className="progress" style={{ marginTop: "0.8rem" }}>
                      <span style={{ width: `${Math.max(8, value * 100)}%` }} />
                    </div>
                  </article>
                ))}
              </div>

              <div className="stack">
                <Panel title="退化项" subtitle="与历史基线对比后的风险项。">
                  {report.regressions.length ? (
                    <div className="result-list">
                      {report.regressions.map((item, index) => (
                        <article className="result-item" key={`${item}-${index}`}>
                          <p>{item}</p>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <div className="empty">没有检测到明显退化。</div>
                  )}
                </Panel>

                <Panel title="建议" subtitle="后端评测器自动生成的建议。">
                  {report.recommendations.length ? (
                    <div className="result-list">
                      {report.recommendations.map((item, index) => (
                        <article className="result-item" key={`${item}-${index}`}>
                          <p>{item}</p>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <div className="empty">当前没有额外建议。</div>
                  )}
                </Panel>
              </div>
            </div>

            <div className="card">
              <div className="card-inner">
                <div className="card-header">
                  <div>
                    <h3 className="card-title">详细结果</h3>
                    <p className="card-subtitle">方便你定位具体是哪个用例没通过。</p>
                  </div>
                </div>
                <div style={{ overflowX: "auto" }}>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Test</th>
                        <th>Passed</th>
                        <th>Scores</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.results.map((item) => (
                        <tr key={item.test_id}>
                          <td className="mono">{item.test_id}</td>
                          <td>{item.passed ? "yes" : "no"}</td>
                          <td className="mono">{Object.entries(item.scores).map(([key, value]) => `${key}:${value}`).join(" | ")}</td>
                          <td>{item.detail || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="empty">还没有评测结果。点击“开始评测”后，这里会展示聚合报告和详细明细。</div>
        )}
      </Panel>
    </div>
  );
}
