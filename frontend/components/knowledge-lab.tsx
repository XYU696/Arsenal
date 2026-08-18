"use client";

import { FormEvent, useEffect, useState } from "react";
import { apiFetch, buildQuery } from "@/lib/api";
import type { KnowledgeSearchResponse, KnowledgeStats, SearchResponse, SearchResult } from "@/lib/types";
import { Hero, Panel, StatusPill } from "@/components/ui";

// Phase A：知识库知识域枚举，用于过滤检索
const KNOWLEDGE_CATEGORIES = [
  { value: "", label: "全部知识域" },
  { value: "refund_policy", label: "退款政策" },
  { value: "return_process", label: "退货流程" },
  { value: "exchange_process", label: "换货流程" },
  { value: "logistics_issue", label: "物流异常" },
  { value: "coupon_compensation", label: "优惠券补偿" },
  { value: "payment_invoice", label: "支付与发票" },
  { value: "after_sales_ticket", label: "售后工单与SLA" },
  { value: "incident", label: "故障与公告" },
  { value: "compliance", label: "话术与合规" }
];

export function KnowledgeLab() {
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [query, setQuery] = useState("退款流程");
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [filterQuery, setFilterQuery] = useState("录音笔退款政策");
  const [category, setCategory] = useState("");
  const [product, setProduct] = useState("");
  const [filtered, setFiltered] = useState<SearchResult[] | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function refreshStats() {
    const next = await apiFetch<KnowledgeStats>("/knowledge/stats");
    setStats(next);
  }

  useEffect(() => {
    refreshStats().catch((err: Error) => setError(err.message));
  }, []);

  async function handleSearch(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    const data = await apiFetch<SearchResponse>(`/search${buildQuery({ query, top_k: 5 })}`, {
      method: "POST"
    });
    setResults(data);
  }

  // Phase A：调用 /knowledge/search，按知识域/产品过滤检索
  async function handleFilteredSearch(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    const body: Record<string, unknown> = { query: filterQuery, top_k: 5 };
    if (category) {
      body.category = category;
    }
    if (product.trim()) {
      body.product = product.trim();
    }
    const data = await apiFetch<KnowledgeSearchResponse>("/knowledge/search", {
      method: "POST",
      body: JSON.stringify(body)
    });
    setFiltered(data.results);
  }

  async function addDocument(event: FormEvent) {
    event.preventDefault();
    setUploading(true);
    setError("");
    setMessage("");

    try {
      const response = await apiFetch<{ message: string }>("/knowledge/add", {
        method: "POST",
        body: JSON.stringify({
          documents: [{ title, content }]
        })
      });
      setMessage(response.message);
      setTitle("");
      setContent("");
      await refreshStats();
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setUploading(false);
    }
  }

  async function uploadFile(file: File) {
    setUploading(true);
    setError("");
    setMessage("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await apiFetch<{ message: string }>("/knowledge/upload", {
        method: "POST",
        body: formData
      });
      setMessage(response.message);
      await refreshStats();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="page">
      <Hero
        title="知识库实验室"
        description="用来验证 RAG 是否真的命中了内容。你可以直接测试检索，也可以临时导入文档，让前端成为一个能看、能试、能加料的小控制台。"
        meta={
          <>
            <StatusPill tone="ok">
              文档片段 {stats?.total_chunks ?? "-"} · {stats?.collection ?? "knowledge_base_v2"}
            </StatusPill>
            <StatusPill>{results?.reranked ? "启用重排" : "等待检索"}</StatusPill>
          </>
        }
      />

      <div className="grid two">
        <Panel title="检索测试" subtitle="向 `/search` 发送查询，验证查询改写与重排结果。">
          <form className="stack" onSubmit={handleSearch}>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：退款规则" />
            <div className="button-row">
              <button className="button" type="submit">开始检索</button>
              <button className="ghost-button" type="button" onClick={() => handleSearch()}>
                刷新结果
              </button>
            </div>
          </form>

          <div className="result-list" style={{ marginTop: "1rem" }}>
            {results?.results?.length ? (
              results.results.map((item, index) => (
                <article key={`${item.title}-${index}`} className="result-item">
                  <div className="split">
                    <h4>{item.title}</h4>
                    <StatusPill>score {item.score}</StatusPill>
                  </div>
                  <p>{item.content}</p>
                </article>
              ))
            ) : (
              <div className="empty">还没有检索结果。先输入一个业务问题试试看。</div>
            )}
          </div>
        </Panel>

        <Panel
          title="过滤检索（Phase A）"
          subtitle="按知识域/产品过滤语义检索，适用于电商售后知识库。"
        >
          <form className="stack" onSubmit={handleFilteredSearch}>
            <input
              value={filterQuery}
              onChange={(event) => setFilterQuery(event.target.value)}
              placeholder="例如：录音笔退款政策"
            />
            <select value={category} onChange={(event) => setCategory(event.target.value)}>
              {KNOWLEDGE_CATEGORIES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <input
              value={product}
              onChange={(event) => setProduct(event.target.value)}
              placeholder="产品过滤（可选），例如：智能录音笔 A1"
            />
            <button className="button" type="submit">
              过滤检索
            </button>
          </form>

          <div className="result-list" style={{ marginTop: "1rem" }}>
            {filtered?.length ? (
              filtered.map((item, index) => (
                <article key={`${item.doc_id}-${index}`} className="result-item">
                  <div className="split">
                    <h4>{item.title}</h4>
                    <StatusPill>score {item.score}</StatusPill>
                  </div>
                  <div className="message-meta">
                    <span>{item.doc_id}</span>
                    <span>{item.category}</span>
                    {item.product ? <span>{item.product}</span> : null}
                    {item.source ? <span>{item.source}</span> : null}
                  </div>
                  <p>{item.content}</p>
                </article>
              ))
            ) : (
              <div className="empty">还没有过滤检索结果。选择知识域后输入问题试试。</div>
            )}
          </div>
        </Panel>

        <Panel title="内容导入" subtitle="支持手工录入单篇文档，也支持上传 txt、md、json。">
          <form className="stack" onSubmit={addDocument}>
            <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="文档标题" />
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="输入文档内容，例如退款政策、配送规则、排障 SOP"
            />
            <div className="button-row">
              <button className="button" type="submit" disabled={uploading || !title.trim() || !content.trim()}>
                {uploading ? "提交中..." : "新增文档"}
              </button>
              <label className="soft-button">
                上传文件
                <input
                  type="file"
                  accept=".txt,.md,.json"
                  style={{ display: "none" }}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) {
                      void uploadFile(file);
                    }
                  }}
                />
              </label>
            </div>
          </form>

          {message ? <div className="status-pill ok" style={{ marginTop: "1rem" }}>{message}</div> : null}
          {error ? <div className="status-pill error" style={{ marginTop: "1rem" }}>{error}</div> : null}
        </Panel>
      </div>
    </div>
  );
}
