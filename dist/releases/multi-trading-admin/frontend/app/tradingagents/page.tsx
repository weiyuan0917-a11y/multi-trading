"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { localAgentGet as apiGet, localAgentPost as apiPost } from "@/lib/local-agent-api";
import { PageShell } from "@/components/ui/page-shell";

type AnalyzeTaskStatus = {
  task_id: string;
  status: "pending" | "running" | "done" | "failed" | string;
  created_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  input?: { symbol?: string; market?: string; question?: string };
  error?: string | null;
  progress_pct?: number;
  progress_stage?: string;
  progress_text?: string;
  progress_updated_at?: string | null;
  heartbeat_at?: string | null;
  progress_events?: Array<{ ts?: string; stage?: string; pct?: number; text?: string }>;
  agent_events?: Array<Record<string, any>>;
  agent_statuses?: Record<string, { team?: string; status?: string; updated_at?: string }>;
  latest_report_section?: { section?: string; agent?: string; content?: string; updated_at?: string } | null;
};

type AnalyzeResult = {
  symbol?: string;
  market?: string;
  question?: string;
  available?: boolean;
  action?: string;
  confidence?: number;
  decision_text?: string;
  reason?: string;
  generated_at?: string;
  stage_reports?: Record<string, string>;
  a_share_template?: string | null;
  fundamental_snapshot_v2?: Record<string, any> | null;
  data_diagnostics?: Record<string, any> | null;
  assistant_message_markdown?: string;
  report_markdown?: string;
};

type AnalyzeStartResp = {
  ok?: boolean;
  async_run?: boolean;
  task?: AnalyzeTaskStatus;
  result?: AnalyzeResult;
};

type AnalyzeResultResp = {
  ok?: boolean;
  ready?: boolean;
  task?: AnalyzeTaskStatus;
  result?: AnalyzeResult;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  taskId?: string;
  taskStatus?: string;
  taskProgress?: AnalyzeTaskStatus;
  result?: AnalyzeResult;
};

type ChatSession = {
  id: string;
  title: string;
  symbol: string;
  market: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
};

type RunningTaskRef = {
  taskId: string;
  sessionId: string;
  messageId: string;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010";
const STORAGE_KEY = "tradingagents_chat_sessions_v1";

const QUESTION_TEMPLATES: Array<{ id: string; label: string; prompt: string }> = [
  { id: "mkt", label: "市场趋势", prompt: "当前市场趋势、技术结构与关键价位是什么？" },
  { id: "news", label: "新闻催化", prompt: "近7天新闻和事件催化有哪些，偏多还是偏空？" },
  { id: "fund", label: "基本面", prompt: "估值、增长、盈利质量和同业对比如何？" },
  { id: "risk", label: "风险清单", prompt: "主要风险点、触发条件与应对建议是什么？" },
  { id: "position", label: "仓位建议", prompt: "给出可执行的仓位建议、止损位和观察指标。" },
  { id: "short", label: "一句话结论", prompt: "请最后给出一句话结论和置信度。" },
];

const RUNNING_PROCESS_STEPS = ["请求入队", "拉取行情与新闻", "多智能体讨论", "生成最终报告"];
const STAGE_LABELS: Record<string, string> = {
  market_report: "市场分析师",
  sentiment_report: "情绪分析师",
  social_report: "社媒分析师",
  news_report: "新闻分析师",
  fundamentals_report: "基本面分析师",
  bull_researcher_report: "多头研究员",
  bear_researcher_report: "空头研究员",
  debate_summary: "辩论总结",
  risk_manager_report: "风险经理",
  portfolio_manager_report: "组合经理",
  analyst_market: "市场分析师",
  analyst_sentiment: "情绪分析师",
  analyst_news: "新闻分析师",
  analyst_fundamentals: "基本面分析师",
  cn_public_context: "A股公共数据上下文 v2",
};

function uid(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeSymbol(input: string): string {
  return String(input || "").trim().toUpperCase();
}

function createEmptySession(symbol: string, market: string): ChatSession {
  const sym = normalizeSymbol(symbol) || "NVDA.US";
  const now = new Date().toISOString();
  return {
    id: uid("sess"),
    title: `${sym} 会话`,
    symbol: sym,
    market: String(market || "us").toLowerCase() || "us",
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

function buildQuestionFromTemplates(templateIds: string[], custom: string): string {
  const selectedPrompts = templateIds
    .map((id) => QUESTION_TEMPLATES.find((item) => item.id === id)?.prompt || "")
    .filter((x) => x);
  const customText = String(custom || "").trim();
  const lines: string[] = [];
  if (selectedPrompts.length) {
    lines.push("请重点回答以下问题：");
    selectedPrompts.forEach((p, idx) => lines.push(`${idx + 1}. ${p}`));
  }
  if (customText) {
    lines.push("补充问题：");
    lines.push(customText);
  }
  if (!lines.length) return "";
  return lines.join("\n");
}

function formatElapsedSince(iso?: string | null): string {
  if (!iso) return "-";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return "-";
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m ${rest}s`;
}

function progressPct(task?: AnalyzeTaskStatus | null): number {
  const raw = Number(task?.progress_pct ?? 0);
  if (!Number.isFinite(raw)) return 0;
  return Math.max(0, Math.min(100, Math.round(raw)));
}

function progressTone(task?: AnalyzeTaskStatus | null): string {
  const stage = String(task?.progress_stage || "");
  if (stage === "failed") return "bg-rose-400";
  if (stage === "done") return "bg-emerald-400";
  return "bg-cyan-300";
}

function stageLabel(stage?: string): string {
  const key = String(stage || "queued");
  const labels: Record<string, string> = {
    queued: "任务排队",
    starting: "启动任务",
    routing: "理解问题与选择分析范围",
    research: "TradingAgents 研究中",
    report: "整理研究材料",
    chat: "大模型 Chat 生成回答",
    finalizing: "写入最终结果",
    done: "分析完成",
    failed: "分析失败",
  };
  return labels[key] || key;
}

function eventLabel(ev: { stage?: string; text?: string }): string {
  const text = String(ev.text || "");
  if (text === "task_started") return "任务已启动";
  return stageLabel(ev.stage);
}

function agentStatusTone(status?: string): string {
  const s = String(status || "pending");
  if (s === "completed") return "border-emerald-500/40 bg-emerald-500/10 text-emerald-200";
  if (s === "in_progress") return "border-cyan-500/40 bg-cyan-500/10 text-cyan-200";
  if (s === "error") return "border-rose-500/40 bg-rose-500/10 text-rose-200";
  return "border-slate-600/70 bg-slate-800/50 text-slate-300";
}

function agentEventLabel(ev: Record<string, any>): string {
  const kind = String(ev.kind || "");
  if (kind === "agent_status") return `${ev.agent || "Agent"}: ${ev.status || "-"}`;
  if (kind === "tool_call") return `Tool: ${ev.name || "-"}`;
  if (kind === "message") return `${ev.message_type || "Message"}: ${String(ev.content || "").slice(0, 80)}`;
  if (kind === "report_section") return `${ev.agent || "Agent"} produced ${ev.section || "report"}`;
  if (kind === "stream_fallback") return `Stream fallback: ${ev.message || ""}`;
  if (kind === "stream_start") return "TradingAgents stream started";
  if (kind === "stream_done") return "TradingAgents stream completed";
  return kind || "event";
}

export default function TradingAgentsPage() {
  const [symbol, setSymbol] = useState("NVDA.US");
  const [market, setMarket] = useState("us");
  const [customQuestion, setCustomQuestion] = useState("");
  const [selectedTemplateIds, setSelectedTemplateIds] = useState<string[]>([]);
  const [task, setTask] = useState<AnalyzeTaskStatus | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [runningTask, setRunningTask] = useState<RunningTaskRef | null>(null);
  const [selectedSessionIds, setSelectedSessionIds] = useState<string[]>([]);
  const [showQuickTemplates, setShowQuickTemplates] = useState(false);
  const [draggingTemplateId, setDraggingTemplateId] = useState<string | null>(null);
  const [sessionPanelCollapsed, setSessionPanelCollapsed] = useState(false);
  const [minimalInputMode, setMinimalInputMode] = useState(true);
  const [inputExpanded, setInputExpanded] = useState(false);
  const chatViewportRef = useRef<HTMLDivElement | null>(null);

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) || null,
    [sessions, activeSessionId]
  );
  const running = Boolean(runningTask?.taskId);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        const initial = createEmptySession(symbol, market);
        setSessions([initial]);
        setActiveSessionId(initial.id);
        return;
      }
      const parsed = JSON.parse(raw) as ChatSession[];
      if (!Array.isArray(parsed) || !parsed.length) {
        const initial = createEmptySession(symbol, market);
        setSessions([initial]);
        setActiveSessionId(initial.id);
        return;
      }
      setSessions(parsed);
      setActiveSessionId(parsed[0]?.id || "");
      const first = parsed[0];
      if (first) {
        setSymbol(first.symbol || "NVDA.US");
        setMarket(first.market || "us");
      }
    } catch {
      const initial = createEmptySession(symbol, market);
      setSessions([initial]);
      setActiveSessionId(initial.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!sessions.length) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  }, [sessions]);

  useEffect(() => {
    if (!activeSession) return;
    setSymbol(activeSession.symbol || "NVDA.US");
    setMarket(activeSession.market || "us");
  }, [activeSession?.id]);

  useEffect(() => {
    if (!chatViewportRef.current) return;
    chatViewportRef.current.scrollTop = chatViewportRef.current.scrollHeight;
  }, [activeSession?.messages, running]);

  useEffect(() => {
    if (!runningTask?.taskId) return;
    const timer = window.setInterval(async () => {
      try {
        const resp = await apiGet<AnalyzeResultResp>(`/tradingagents/result/${runningTask.taskId}`, {
          timeoutMs: 10000,
          retries: 0,
          cacheTtlMs: 0,
        });
        if (resp?.task) setTask(resp.task);
        if (resp?.task?.status) {
          setSessions((prev) =>
            prev.map((sess) => {
              if (sess.id !== runningTask.sessionId) return sess;
              return {
                ...sess,
                messages: sess.messages.map((msg) =>
                  msg.id === runningTask.messageId
                    ? { ...msg, taskStatus: String(resp.task?.status || ""), taskProgress: resp.task }
                    : msg
                ),
              };
            })
          );
        }
        if (resp?.ready) {
          const doneResult = resp.result || null;
          setSessions((prev) =>
            prev.map((sess) => {
              if (sess.id !== runningTask.sessionId) return sess;
              return {
                ...sess,
                updatedAt: new Date().toISOString(),
                messages: sess.messages.map((msg) =>
                  msg.id === runningTask.messageId
                    ? {
                        ...msg,
                        content:
                          doneResult?.assistant_message_markdown ||
                          doneResult?.report_markdown ||
                          doneResult?.decision_text ||
                          "暂无输出",
                        taskStatus: String(resp.task?.status || "done"),
                        taskProgress: resp.task || undefined,
                        result: doneResult || undefined,
                      }
                    : msg
                ),
              };
            })
          );
          setRunningTask(null);
          window.clearInterval(timer);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setRunningTask(null);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [runningTask]);

  function updateSession(sessionId: string, updater: (session: ChatSession) => ChatSession) {
    setSessions((prev) => prev.map((sess) => (sess.id === sessionId ? updater(sess) : sess)));
  }

  function createNewSession() {
    const newSession = createEmptySession(symbol, market);
    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(newSession.id);
    setSelectedSessionIds([]);
    setCustomQuestion("");
    setSelectedTemplateIds([]);
  }

  function toggleSessionSelection(sessionId: string) {
    setSelectedSessionIds((prev) =>
      prev.includes(sessionId) ? prev.filter((id) => id !== sessionId) : [...prev, sessionId]
    );
  }

  function selectAllSessions() {
    setSelectedSessionIds(sessions.map((s) => s.id));
  }

  function clearSessionSelection() {
    setSelectedSessionIds([]);
  }

  function deleteSelectedSessions() {
    const selectedSet = new Set(selectedSessionIds);
    if (!selectedSet.size) return;
    const deletingActive = activeSessionId && selectedSet.has(activeSessionId);
    const deletingRunning = runningTask?.sessionId && selectedSet.has(runningTask.sessionId);
    const remaining = sessions.filter((s) => !selectedSet.has(s.id));
    if (!remaining.length) {
      const fallback = createEmptySession(symbol, market);
      setSessions([fallback]);
      setActiveSessionId(fallback.id);
    } else {
      setSessions(remaining);
      if (deletingActive) {
        setActiveSessionId(remaining[0].id);
      }
    }
    if (deletingRunning) {
      setRunningTask(null);
      setTask(null);
    }
    setSelectedSessionIds([]);
  }

  function toggleTemplate(templateId: string) {
    setSelectedTemplateIds((prev) =>
      prev.includes(templateId) ? prev.filter((x) => x !== templateId) : [...prev, templateId]
    );
  }

  function removeTemplateTag(templateId: string) {
    setSelectedTemplateIds((prev) => prev.filter((x) => x !== templateId));
  }

  function reorderTemplateTags(dragId: string, hoverId: string) {
    if (!dragId || !hoverId || dragId === hoverId) return;
    setSelectedTemplateIds((prev) => {
      const from = prev.indexOf(dragId);
      const to = prev.indexOf(hoverId);
      if (from < 0 || to < 0) return prev;
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }

  async function startAnalyze() {
    const sym = String(symbol || "").trim().toUpperCase();
    if (!sym) {
      setError("请输入股票代码，例如 NVDA.US 或 700.HK");
      return;
    }
    if (!activeSessionId) {
      createNewSession();
      return;
    }
    if (runningTask?.taskId) {
      setError("当前已有任务执行中，请等待完成后再发起新问题。");
      return;
    }
    const baseQuestion = buildQuestionFromTemplates(selectedTemplateIds, customQuestion);
    if (!baseQuestion.trim()) {
      setError("请输入一个具体问题，或点击 + 选择一个预设问题。");
      return;
    }
    const historySummary = (activeSession?.messages || [])
      .slice(-6)
      .map((m) => `${m.role === "user" ? "用户" : "助手"}：${m.content}`)
      .join("\n\n")
      .slice(0, 2000);
    const finalQuestion = historySummary
      ? `${baseQuestion}\n\n【会话上下文（最近若干轮）】\n${historySummary}\n\n请结合上下文继续回答。`
      : baseQuestion;

    const userMessage: ChatMessage = {
      id: uid("msg"),
      role: "user",
      content: baseQuestion,
      createdAt: new Date().toISOString(),
    };
    const assistantMessage: ChatMessage = {
      id: uid("msg"),
      role: "assistant",
      content: "任务已提交，正在分析中...",
      createdAt: new Date().toISOString(),
      taskStatus: "pending",
      taskProgress: {
        task_id: "",
        status: "pending",
        progress_pct: 0,
        progress_stage: "queued",
        progress_text: "任务排队中",
        progress_updated_at: new Date().toISOString(),
        heartbeat_at: new Date().toISOString(),
      },
    };
    updateSession(activeSessionId, (session) => ({
      ...session,
      symbol: sym,
      market,
      title: `${sym} 会话`,
      updatedAt: new Date().toISOString(),
      messages: [...session.messages, userMessage, assistantMessage],
    }));

    setSubmitting(true);
    setError("");
    setShowQuickTemplates(false);
    try {
      const resp = await apiPost<AnalyzeStartResp>(
        "/tradingagents/analyze",
        {
          symbol: sym,
          market,
          question: finalQuestion,
          selected_template_ids: selectedTemplateIds,
          async_run: true,
        },
        { timeoutMs: 15000, retries: 0 }
      );
      const nextTask = resp.task || null;
      setTask(nextTask);
      if (nextTask?.task_id) {
        setRunningTask({ taskId: nextTask.task_id, sessionId: activeSessionId, messageId: assistantMessage.id });
        updateSession(activeSessionId, (session) => ({
          ...session,
          messages: session.messages.map((msg) =>
            msg.id === assistantMessage.id
              ? {
                  ...msg,
                  taskId: nextTask.task_id,
                  taskStatus: String(nextTask.status || "pending"),
                  taskProgress: nextTask,
                }
              : msg
          ),
        }));
      }
      setCustomQuestion("");
      if (minimalInputMode) setInputExpanded(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      updateSession(activeSessionId, (session) => ({
        ...session,
        messages: session.messages.map((msg) =>
          msg.id === assistantMessage.id ? { ...msg, content: `任务提交失败：${String(e)}`, taskStatus: "failed" } : msg
        ),
      }));
    } finally {
      setSubmitting(false);
    }
  }

  function download(taskId: string, format: "md" | "json") {
    if (!taskId) return;
    const url = `${API_BASE}/tradingagents/result/${taskId}/download?format=${format}`;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-cyan-950/25">
        <h1 className="text-2xl font-bold tracking-tight text-white">TradingAgents 智能体</h1>
        <p className="mt-1 text-sm text-slate-300">
          输入股票后触发多智能体研判，输出完整分析报告，并支持一键下载 Markdown / JSON。
        </p>
      </div>

      <section className={`grid gap-4 ${sessionPanelCollapsed ? "lg:grid-cols-1" : "lg:grid-cols-12"}`}>
        {!sessionPanelCollapsed ? (
        <aside className="panel lg:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-[10px] font-semibold text-slate-100">会话列表</h2>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={createNewSession}
                className="rounded-md border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-[10px] text-cyan-200 hover:bg-cyan-500/20"
              >
                新建会话
              </button>
              <button
                type="button"
                onClick={() => setSessionPanelCollapsed(true)}
                className="rounded-md border border-slate-600/70 bg-slate-800/70 px-2 py-1 text-[10px] text-slate-200 hover:bg-slate-700"
                title="折叠会话列表"
              >
                折叠
              </button>
            </div>
          </div>
          <div className="mb-2 flex flex-wrap items-center gap-1">
            <button
              type="button"
              onClick={selectAllSessions}
              disabled={!sessions.length}
              className="rounded border border-slate-600/70 bg-slate-800/70 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            >
              全选
            </button>
            <button
              type="button"
              onClick={clearSessionSelection}
              disabled={!selectedSessionIds.length}
              className="rounded border border-slate-600/70 bg-slate-800/70 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="button"
              onClick={deleteSelectedSessions}
              disabled={!selectedSessionIds.length}
              className="rounded border border-rose-500/50 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
            >
              删除已选（{selectedSessionIds.length}）
            </button>
          </div>
          <div className="space-y-2">
            {sessions.map((sess) => {
              const active = sess.id === activeSessionId;
              const selected = selectedSessionIds.includes(sess.id);
              return (
                <button
                  key={sess.id}
                  type="button"
                  onClick={() => setActiveSessionId(sess.id)}
                  className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                    active
                      ? "border-cyan-500/50 bg-cyan-500/10"
                      : "border-slate-700/70 bg-slate-900/70 hover:bg-slate-800/80"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="truncate text-xs font-medium text-slate-100">{sess.title}</div>
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={(e) => {
                        e.stopPropagation();
                        toggleSessionSelection(sess.id);
                      }}
                      onClick={(e) => e.stopPropagation()}
                      className="mt-0.5"
                      aria-label={`选择会话 ${sess.title}`}
                    />
                  </div>
                  <div className="mt-1 text-[10px] text-slate-400">
                    {sess.market.toUpperCase()} · {sess.messages.length} 条消息
                  </div>
                </button>
              );
            })}
          </div>
        </aside>
        ) : null}

        <div className={`panel ${sessionPanelCollapsed ? "" : "lg:col-span-10"}`}>
          <div className="flex h-[86vh] flex-col gap-2.5 rounded-2xl border border-slate-700/70 bg-slate-950/70 p-3">
            <div className="flex items-center justify-between rounded-xl border border-slate-700/70 bg-slate-900/70 px-3 py-2">
              <div className="text-sm font-semibold text-slate-100">AI 助手 · TradingAgents</div>
              <div className="flex items-center gap-2">
                <div className="text-xs text-slate-400">会话：{activeSession?.title || "未选择"} / 状态：{task?.status || "idle"}</div>
                <button
                  type="button"
                  onClick={() => setSessionPanelCollapsed((v) => !v)}
                  className="rounded-md border border-slate-600/70 bg-slate-800/70 px-2 py-1 text-[10px] text-slate-200 hover:bg-slate-700"
                >
                  {sessionPanelCollapsed ? "展开会话列表" : "隐藏会话列表"}
                </button>
              </div>
            </div>

            <div
              ref={chatViewportRef}
              className="flex-1 space-y-3 overflow-auto rounded-xl border border-slate-700/80 bg-gradient-to-b from-slate-950 to-slate-900/90 p-4"
            >
              {!activeSession?.messages?.length ? (
                <div className="text-sm text-slate-400">还没有消息，先在下方输入并发送。</div>
              ) : (
                activeSession.messages.map((msg) => (
                  <div key={msg.id} className={`flex items-end gap-2 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                    {msg.role === "assistant" ? (
                      <div className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-cyan-500/25 text-[11px] font-semibold text-cyan-100 ring-1 ring-cyan-400/40">
                        AI
                      </div>
                    ) : null}
                    <div
                      className={`max-w-[88%] rounded-2xl border px-3 py-2.5 shadow-sm ${
                        msg.role === "user"
                          ? "border-violet-400/50 bg-violet-400/20"
                          : "border-slate-500/35 bg-white/5"
                      }`}
                    >
                      <div className="mb-1 flex items-center justify-between gap-3">
                        <span className="text-xs font-medium text-slate-200">{msg.role === "user" ? "你" : "TradingAgents"}</span>
                        <span className="text-[11px] text-slate-400">{msg.taskStatus || "-"}</span>
                      </div>
                      <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap text-xs leading-6 text-slate-100">
                        {msg.content}
                      </pre>
                      {msg.role === "assistant" && msg.taskId && msg.result ? (
                        <>
                          <details className="mt-2 border-t border-slate-600/40 pt-2">
                            <summary className="cursor-pointer text-xs text-cyan-200">分析过程</summary>
                            {Object.entries(msg.result?.stage_reports || {}).filter(([, c]) => String(c || "").trim()).length ? (
                              <div className="mt-2 space-y-1.5">
                                {Object.entries(msg.result?.stage_reports || {})
                                  .filter(([, c]) => String(c || "").trim())
                                  .map(([key, content]) => (
                                    <details key={key} className="pl-2">
                                      <summary className="cursor-pointer text-[11px] text-cyan-300">• {STAGE_LABELS[key] || key}</summary>
                                      <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-slate-300">
                                        {String(content || "")}
                                      </pre>
                                    </details>
                                  ))}
                              </div>
                            ) : (
                              <div className="mt-2 space-y-1">
                                {RUNNING_PROCESS_STEPS.map((step, idx) => (
                                  <div key={step} className="text-[11px] text-slate-300">
                                    {idx + 1}. {step}
                                  </div>
                                ))}
                              </div>
                            )}
                          </details>
                          <div className="mt-2 flex gap-2">
                            <button
                              type="button"
                              onClick={() => download(msg.taskId || "", "md")}
                              className="rounded-md border border-slate-500/70 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
                            >
                              下载 Markdown
                            </button>
                            <button
                              type="button"
                              onClick={() => download(msg.taskId || "", "json")}
                              className="rounded-md border border-slate-500/70 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
                            >
                              下载 JSON
                            </button>
                          </div>
                        </>
                      ) : null}
                      {msg.role === "assistant" && !msg.result && (msg.taskStatus === "pending" || msg.taskStatus === "running") ? (
                        <details className="mt-2 border-t border-slate-600/40 pt-2" open>
                          <summary className="cursor-pointer text-xs text-cyan-200">分析过程（进行中）</summary>
                          <div className="mt-2 rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-2">
                            <div className="mb-1 flex items-center justify-between gap-2 text-[11px]">
                              <span className="font-medium text-cyan-100">
                                {msg.taskProgress?.progress_text || "任务运行中"}
                              </span>
                              <span className="text-cyan-200">{progressPct(msg.taskProgress)}%</span>
                            </div>
                            <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
                              <div
                                className={`h-full rounded-full transition-all duration-500 ${progressTone(msg.taskProgress)}`}
                                style={{ width: `${progressPct(msg.taskProgress)}%` }}
                              />
                            </div>
                            <div className="mt-2 grid gap-1 text-[11px] text-slate-400 sm:grid-cols-3">
                              <div>阶段：{stageLabel(msg.taskProgress?.progress_stage)}</div>
                              <div>已耗时：{formatElapsedSince(msg.taskProgress?.started_at || msg.createdAt)}</div>
                              <div>心跳：{formatElapsedSince(msg.taskProgress?.heartbeat_at)} 前</div>
                            </div>
                            {msg.taskProgress?.progress_events?.length ? (
                              <div className="mt-2 space-y-1 border-t border-cyan-500/15 pt-2">
                                {msg.taskProgress.progress_events.slice(-8).map((ev, idx) => (
                                  <div key={`${ev.ts || "event"}-${idx}`} className="grid grid-cols-[46px_1fr_auto] gap-2 text-[11px] text-slate-300">
                                    <span className="text-cyan-300">{Number(ev.pct || 0)}%</span>
                                    <span>{eventLabel(ev)}</span>
                                    <span className="text-slate-500">{formatElapsedSince(ev.ts)} 前</span>
                                  </div>
                                ))}
                              </div>
                            ) : null}
                            {msg.taskProgress?.agent_statuses && Object.keys(msg.taskProgress.agent_statuses).length ? (
                              <div className="mt-2 border-t border-cyan-500/15 pt-2">
                                <div className="mb-1 text-[11px] font-medium text-cyan-100">Agent Progress</div>
                                <div className="grid gap-1 sm:grid-cols-2">
                                  {Object.entries(msg.taskProgress.agent_statuses).map(([agent, row]) => (
                                    <div
                                      key={agent}
                                      className={`rounded border px-2 py-1 text-[11px] ${agentStatusTone(row?.status)}`}
                                    >
                                      <div className="font-medium">{agent}</div>
                                      <div className="text-[10px] opacity-80">{row?.team || "-"} · {row?.status || "pending"}</div>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ) : null}
                            {msg.taskProgress?.agent_events?.length ? (
                              <div className="mt-2 border-t border-cyan-500/15 pt-2">
                                <div className="mb-1 text-[11px] font-medium text-cyan-100">Messages & Tools</div>
                                <div className="max-h-36 space-y-1 overflow-auto">
                                  {msg.taskProgress.agent_events.slice(-10).map((ev, idx) => (
                                    <div key={`${ev.ts || "agent"}-${idx}`} className="grid grid-cols-[1fr_auto] gap-2 text-[11px] text-slate-300">
                                      <span className="truncate">{agentEventLabel(ev)}</span>
                                      <span className="text-slate-500">{formatElapsedSince(ev.ts)} 前</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ) : null}
                            {msg.taskProgress?.latest_report_section?.content ? (
                              <div className="mt-2 border-t border-cyan-500/15 pt-2">
                                <div className="mb-1 text-[11px] font-medium text-cyan-100">
                                  Current Report · {msg.taskProgress.latest_report_section.agent || msg.taskProgress.latest_report_section.section}
                                </div>
                                <pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-slate-950/70 p-2 text-[11px] leading-5 text-slate-300">
                                  {msg.taskProgress.latest_report_section.content}
                                </pre>
                              </div>
                            ) : null}
                            <div className="mt-2 text-[11px] leading-relaxed text-slate-400">
                              当前显示的是任务阶段和心跳状态；模型内部推理不会展开，但你可以据此判断它是否仍在分析。
                            </div>
                          </div>
                        </details>
                      ) : null}
                    </div>
                    {msg.role === "user" ? (
                      <div className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-violet-400/25 text-[11px] font-semibold text-violet-100 ring-1 ring-violet-300/40">
                        我
                      </div>
                    ) : null}
                  </div>
                ))
              )}
            </div>

            <div className="relative rounded-xl border border-slate-700/80 bg-slate-900/85 p-2 shadow-[0_6px_24px_rgba(2,6,23,0.4)]">
              <div className="mb-1.5 w-full rounded-xl border border-slate-700 bg-slate-950 px-2.5 py-1.5 ring-cyan-500/40 focus-within:ring-2">
                {!minimalInputMode || inputExpanded ? (
                  <div className="mb-1 text-[11px] text-slate-400">输入框字段（已并入同一输入框）</div>
                ) : null}
                <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                  <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/40 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-100">
                    代码
                    <input
                      className="w-24 border-0 bg-transparent p-0 text-[11px] text-indigo-100 outline-none placeholder:text-indigo-300/60"
                      value={symbol}
                      onChange={(e) => setSymbol(e.target.value)}
                      placeholder="NVDA.US"
                    />
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/40 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-100">
                    市场
                    <select
                      className="border-0 bg-transparent p-0 text-[11px] text-indigo-100 outline-none"
                      value={market}
                      onChange={(e) => setMarket(e.target.value)}
                    >
                      <option value="us">US 美股</option>
                      <option value="hk">HK 港股</option>
                      <option value="cn">CN A股</option>
                    </select>
                  </span>
                  {selectedTemplateIds.length ? (
                    selectedTemplateIds.map((id) => {
                      const tpl = QUESTION_TEMPLATES.find((x) => x.id === id);
                      if (!tpl) return null;
                      return (
                        <span
                          key={id}
                          draggable
                          onDragStart={() => setDraggingTemplateId(id)}
                          onDragEnd={() => setDraggingTemplateId(null)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => {
                            e.preventDefault();
                            if (draggingTemplateId) reorderTemplateTags(draggingTemplateId, id);
                            setDraggingTemplateId(null);
                          }}
                          className={`inline-flex cursor-move items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${
                            draggingTemplateId === id
                              ? "border-violet-400/60 bg-violet-500/20 text-violet-100"
                              : "border-cyan-500/40 bg-cyan-500/10 text-cyan-100"
                          }`}
                          title="可拖拽排序"
                        >
                          <span className="text-cyan-300">⋮⋮</span>
                          {tpl.label}
                          <button
                            type="button"
                            onClick={() => removeTemplateTag(id)}
                            className="rounded-full px-1 text-cyan-200 hover:bg-cyan-500/20"
                            title={`删除标签 ${tpl.label}`}
                            aria-label={`删除标签 ${tpl.label}`}
                          >
                            ×
                          </button>
                        </span>
                      );
                    })
                  ) : (
                    <span className="text-[11px] text-slate-500">暂无标签，点击 + 选择预设问题。</span>
                  )}
                </div>
                <textarea
                  rows={minimalInputMode && !inputExpanded ? 1 : 3}
                  className={`w-full border-0 bg-transparent p-0 text-[11px] text-slate-100 outline-none placeholder:text-slate-500 ${
                    minimalInputMode && !inputExpanded ? "min-h-[22px] max-h-[22px] resize-none overflow-hidden" : "min-h-[36px]"
                  }`}
                  value={customQuestion}
                  onChange={(e) => setCustomQuestion(e.target.value)}
                  onFocus={() => {
                    if (minimalInputMode) setInputExpanded(true);
                  }}
                  onBlur={() => {
                    if (minimalInputMode && !String(customQuestion || "").trim()) setInputExpanded(false);
                  }}
                  placeholder="这里输入补充追问；字段与标签已并入同一输入框并自动参与提问"
                />
              </div>
              {showQuickTemplates ? (
                <div className="absolute bottom-24 left-3 right-3 z-10 rounded-2xl border border-slate-600/80 bg-slate-950/95 p-3 shadow-2xl">
                  <div className="mb-2 text-xs text-slate-300">预设问题（可多选）</div>
                  <div className="grid gap-1 md:grid-cols-2">
                    {QUESTION_TEMPLATES.map((tpl) => {
                      const checked = selectedTemplateIds.includes(tpl.id);
                      return (
                        <label key={tpl.id} className="flex items-start gap-2 rounded px-2 py-1 hover:bg-slate-800/70">
                          <input type="checkbox" checked={checked} onChange={() => toggleTemplate(tpl.id)} className="mt-1" />
                          <span>
                            <span className="block text-xs font-medium text-slate-100">{tpl.label}</span>
                            <span className="block text-[11px] text-slate-400">{tpl.prompt}</span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ) : null}
              <div className="mt-1.5 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setMinimalInputMode((v) => {
                      const next = !v;
                      if (!next) setInputExpanded(true);
                      if (next && !String(customQuestion || "").trim()) setInputExpanded(false);
                      return next;
                    });
                  }}
                  className="rounded-md border border-slate-600/80 bg-slate-800 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-700"
                  title="切换输入框模式"
                >
                  {minimalInputMode ? "极简模式" : "普通模式"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowQuickTemplates((v) => !v)}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-600/80 bg-slate-800 text-lg text-slate-200 hover:bg-slate-700"
                  title="预设问题"
                >
                  +
                </button>
                <button
                  type="button"
                  onClick={startAnalyze}
                  disabled={submitting || running}
                  className="rounded-full border border-violet-500/50 bg-violet-500/15 px-4 py-2 text-sm font-medium text-violet-100 transition hover:bg-violet-500/25 disabled:opacity-50"
                >
                  {submitting ? "提交中..." : running ? "分析中..." : "➤ 发送"}
                </button>
                <span className="text-xs text-slate-400">底部输入，上方输出，可连续追问。</span>
              </div>
              {error ? (
                <div className="mt-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</div>
              ) : null}
              {task?.error ? (
                <div className="mt-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
                  {task.error}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </PageShell>
  );
}
