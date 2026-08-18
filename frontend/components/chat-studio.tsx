"use client";

import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { ChatMessage, ChatResponse, HealthResponse, TraceStep } from "@/lib/types";
import { Hero, Panel, StatusPill } from "@/components/ui";

const starterPrompts = [
  "我的订单什么时候到？",
  "为什么我的订阅被重复扣费了？",
  "应用登录一直报 401 错误，怎么处理？",
  "帮我解释一下退款审核周期。"
];

function createVisitorId() {
  return `web-${Math.random().toString(36).slice(2, 10)}`;
}

// Trace V1：节点名 -> 中文标签，用于轨迹时间线展示
const TRACE_NODE_LABELS: Record<string, string> = {
  load_state: "加载会话",
  classify_or_resume: "任务分类",
  agent: "Agent 决策",
  tool: "工具调用",
  handoff: "转人工",
  save_state: "保存状态"
};

function traceDecisionText(step: TraceStep): string {
  if (step.node === "tool") {
    return `执行 ${step.tool_name ?? "未知工具"}`;
  }
  if (step.node === "handoff") {
    return step.decision === "ticket_created" ? "已创建人工工单" : "转人工但工单创建失败";
  }
  const decisionMap: Record<string, string> = {
    ask_followup: "追问用户",
    call_tool: `调用 ${step.tool_name ?? "工具"}`,
    handoff: "转人工",
    finish: "给出最终答复"
  };
  return decisionMap[step.decision] ?? step.decision;
}

function TraceTimeline({ steps }: { steps?: TraceStep[] }) {
  if (!steps || steps.length === 0) {
    return <div className="muted">本次回复没有执行轨迹（可能走了兜底链路）。</div>;
  }
  return (
    <ol className="trace-list">
      {steps.map((step, index) => {
        const hasArgs =
          step.tool_args &&
          typeof step.tool_args === "object" &&
          Object.keys(step.tool_args).length > 0;
        const hasResult =
          step.tool_result &&
          (typeof step.tool_result !== "object" || Object.keys(step.tool_result).length > 0);
        return (
          <li key={index} className="trace-item">
            <span className={`trace-node trace-node-${step.node}`}>
              {TRACE_NODE_LABELS[step.node] ?? step.node}
            </span>
            <div className="trace-body">
              <div className="trace-title">
                <span>{traceDecisionText(step)}</span>
                <span className="muted">
                  {step.latency_ms != null ? `${step.latency_ms} ms` : ""}
                </span>
              </div>
              {step.reasoning ? <div className="muted">{step.reasoning}</div> : null}
              {step.detail ? <div className="mono muted">{step.detail}</div> : null}
              {hasArgs ? (
                <pre className="trace-json">{JSON.stringify(step.tool_args, null, 2)}</pre>
              ) : null}
              {hasResult ? (
                <pre className="trace-json">
                  {typeof step.tool_result === "string"
                    ? step.tool_result
                    : JSON.stringify(step.tool_result, null, 2)}
                </pre>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ChatStudio() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [convId, setConvId] = useState<string>("");
  const [userId, setUserId] = useState<string>("");
  const [sending, setSending] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    const storedUserId = window.localStorage.getItem("echomind-user-id") || createVisitorId();
    window.localStorage.setItem("echomind-user-id", storedUserId);
    setUserId(storedUserId);
  }, []);

  useEffect(() => {
    apiFetch<HealthResponse>("/health")
      .then(setHealth)
      .catch((err: Error) => setError(err.message));
  }, []);

  const latestAssistant = useMemo(
    () => [...messages].reverse().find((item) => item.role === "assistant"),
    [messages]
  );

  async function sendMessage(rawMessage?: string) {
    const message = (rawMessage ?? input).trim();
    if (!message || sending || !userId) {
      return;
    }

    setSending(true);
    setError("");
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setInput("");

    try {
      const result = await apiFetch<ChatResponse>("/chat", {
        method: "POST",
        body: JSON.stringify({
          message,
          user_id: userId,
          conv_id: convId || undefined
        })
      });

      setConvId(result.conv_id);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.response,
          intent: result.intent,
          agentType: result.agent_type,
          latencyMs: result.latency_ms,
          escalated: result.escalated,
          knowledgeUsed: result.knowledge_used,
          ticketNo: result.ticket_no,
          handoffQueue: result.handoff_queue,
          trace: result.trace
        }
      ]);
    } catch (err) {
      const messageText = err instanceof Error ? err.message : "请求失败";
      setError(messageText);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page">
      <Hero
        title="多 Agent 智能客服工作台"
        description="把对话、Agent 路由、知识库命中和响应诊断放在一个屏幕里，既适合业务演示，也方便前后端联调。"
        meta={
          <>
            <StatusPill tone={health?.status === "ok" ? "ok" : "warn"}>
              服务状态 {health?.status ?? "checking"}
            </StatusPill>
            <StatusPill>{health ? `${Object.keys(health.agents).length} agents ready` : "加载中"}</StatusPill>
            {convId ? <StatusPill>会话 {convId.slice(0, 8)}</StatusPill> : null}
          </>
        }
      />

      <div className="grid main">
        <Panel
          title="快速开始"
          subtitle="这里放几条典型问题，便于你快速观察不同 Agent 的处理风格。"
        >
          <div className="stack">
            {starterPrompts.map((prompt) => (
              <button
                key={prompt}
                className="prompt-chip"
                onClick={() => sendMessage(prompt)}
                disabled={sending}
              >
                {prompt}
              </button>
            ))}
            <div className="detail-item">
              <strong>当前用户标识</strong>
              <div className="mono muted">{userId || "正在生成..."}</div>
            </div>
            <div className="detail-item">
              <strong>对接后端</strong>
              <div className="muted">浏览器会直接请求当前主机的 `8000` 端口；如果部署地址不同，可用 `NEXT_PUBLIC_API_BASE_URL` 覆盖。</div>
            </div>
          </div>
        </Panel>

        <Panel
          title="会话窗口"
          subtitle="向后端 `/chat` 发送真实请求，返回结果会附带意图、Agent 类型和知识库使用情况。"
          action={<StatusPill>{sending ? "生成中" : "Ready"}</StatusPill>}
        >
          <div className="stack">
            <div className="message-list">
              {messages.length === 0 ? (
                <div className="empty">
                  这里还没有消息。你可以先试试订单、退款、登录故障之类的问题，最容易看出多 Agent 分流效果。
                </div>
              ) : (
                messages.map((message, index) => (
                  <article key={`${message.role}-${index}`} className={`message ${message.role}`}>
                    {message.content}
                    {message.role === "assistant" ? (
                      <>
                        <div className="message-meta">
                          <span>intent: {message.intent}</span>
                          <span>agent: {message.agentType}</span>
                          <span>latency: {message.latencyMs} ms</span>
                          <span>knowledge: {message.knowledgeUsed ? "used" : "no"}</span>
                          <span>escalated: {message.escalated ? "yes" : "no"}</span>
                          {message.ticketNo ? <span>ticket: {message.ticketNo}</span> : null}
                        </div>
                        {/* Trace V1：每条回复下方可展开查看完整执行轨迹 */}
                        <details className="trace-details">
                          <summary>执行轨迹（{message.trace?.length ?? 0} 步）</summary>
                          <TraceTimeline steps={message.trace} />
                        </details>
                      </>
                    ) : null}
                  </article>
                ))
              )}
            </div>

            <div className="stack">
              <textarea
                placeholder="输入一条客服请求，例如：为什么我昨天被扣了两次钱？"
                value={input}
                onChange={(event) => setInput(event.target.value)}
              />
              <div className="button-row">
                <button className="button" onClick={() => sendMessage()} disabled={sending || !input.trim()}>
                  {sending ? "发送中..." : "发送消息"}
                </button>
                <button
                  className="ghost-button"
                  onClick={() => {
                    setMessages([]);
                    setConvId("");
                    setError("");
                  }}
                  disabled={sending}
                >
                  新建会话
                </button>
              </div>
              {error ? <div className="status-pill error">{error}</div> : null}
            </div>
          </div>
        </Panel>

        <Panel
          title="诊断侧栏"
          subtitle="最近一次回答的路由元信息会显示在这里，方便你判断系统当前的推理路径。"
        >
          {/* Trace V1：侧栏顶部展示最近一次回答的完整执行轨迹 */}
          <div className="stack">
            <strong>最新执行轨迹</strong>
            <TraceTimeline steps={latestAssistant?.trace} />
          </div>
          <div className="detail-list">
            <div className="detail-item">
              <strong>最近一次意图</strong>
              <div className="muted">{latestAssistant?.intent ?? "尚未产生回答"}</div>
            </div>
            <div className="detail-item">
              <strong>执行 Agent</strong>
              <div className="muted">{latestAssistant?.agentType ?? "尚未路由"}</div>
            </div>
            <div className="detail-item">
              <strong>知识库命中</strong>
              <div className="muted">{latestAssistant ? (latestAssistant.knowledgeUsed ? "已使用 RAG" : "未使用") : "无"}</div>
            </div>
            <div className="detail-item">
              <strong>升级状态</strong>
              <div className="muted">{latestAssistant ? (latestAssistant.escalated ? "建议升级处理" : "无需升级") : "无"}</div>
            </div>
            <div className="detail-item">
              <strong>人工工单</strong>
              <div className="muted">
                {latestAssistant?.ticketNo
                  ? `${latestAssistant.ticketNo}${latestAssistant.handoffQueue ? ` · ${latestAssistant.handoffQueue}` : ""}`
                  : "未创建"}
              </div>
            </div>
            <div className="detail-item">
              <strong>响应耗时</strong>
              <div className="muted">{latestAssistant?.latencyMs ? `${latestAssistant.latencyMs} ms` : "无"}</div>
            </div>
            <div className="detail-item">
              <strong>适合联调的观察点</strong>
              <div className="muted">
                问退款类问题时应更容易路由到 billing；问报错或崩溃类问题时更容易路由到 technical。
              </div>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
