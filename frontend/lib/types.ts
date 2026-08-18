export type ChatRole = "user" | "assistant";

// Trace V1：Agent 执行轨迹中的一步，与后端 RuntimeStepResult.steps 字段对齐
export interface TraceStep {
  node: string;
  decision: string;
  reasoning?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  tool_result?: Record<string, unknown> | string;
  detail?: string;
  latency_ms?: number;
  loop_count?: number;
}

export interface ChatMessage {
  role: ChatRole;
  content: string;
  intent?: string;
  agentType?: string;
  latencyMs?: number;
  escalated?: boolean;
  knowledgeUsed?: boolean;
  ticketNo?: string;
  handoffQueue?: string;
  trace?: TraceStep[];
}

export interface ChatResponse {
  conv_id: string;
  response: string;
  intent: string;
  agent_type: string;
  escalated: boolean;
  latency_ms: number;
  knowledge_used: boolean;
  ticket_no?: string;
  handoff_queue?: string;
  trace?: TraceStep[];
}

export interface AgentStats {
  total: number;
  success_rate: number;
  avg_ms: number;
  monitor_penalty: number;
  routing_score: number;
}

export interface HealthResponse {
  status: string;
  agents: Record<string, AgentStats>;
}

export interface SearchResult {
  title: string;
  content: string;
  score: number;
  chunk?: number;
  doc_id?: string;
  category?: string;
  product?: string;
  source?: string;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  reranked: boolean;
}

export interface KnowledgeSearchResponse {
  query: string;
  top_k: number;
  where: Record<string, unknown> | null;
  results: SearchResult[];
}

export interface SkillSummaryItem {
  name: string;
  description: string;
  path: string;
  keywords: string[];
  agents: string[];
  enabled: boolean;
  content_chars: number;
}

export interface SkillsResponse {
  root_dir: string;
  count: number;
  skills: SkillSummaryItem[];
  errors: string[];
}

export interface ToolStats {
  total: number;
  success_rate: number;
  avg_latency_ms: number;
  consecutive_fails: number;
  circuit_state: string;
}

export interface MonitorAlert {
  severity: "info" | "warning" | "error" | "critical";
  metric: string;
  message: string;
  value: number;
  threshold: number;
  ts: string;
  resolved: boolean;
}

export interface MonitorSuggestion {
  title: string;
  action: string;
  priority: number;
}

export interface MonitorResponse {
  agent_stats: Record<string, AgentStats>;
  tool_stats: Record<string, ToolStats>;
  active_alerts: MonitorAlert[];
  suggestions: MonitorSuggestion[];
}

export interface KnowledgeStats {
  total_chunks: number;
  collection?: string;
}

export interface EvalResultItem {
  test_id: string;
  passed: boolean;
  scores: Record<string, number>;
  detail: string;
  metadata: Record<string, unknown>;
}

export interface EvalResponse {
  pass_rate: number;
  total: number;
  passed: number;
  avg_scores: Record<string, number>;
  regressions: string[];
  recommendations: string[];
  results: EvalResultItem[];
}
