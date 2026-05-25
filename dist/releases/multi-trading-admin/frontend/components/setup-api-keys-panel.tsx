"use client";

import { useCallback, useEffect, useState } from "react";
import {
  localAgentDelete as apiDelete,
  localAgentGet as apiGet,
  localAgentPost as apiPost,
  localAgentPut as apiPut,
} from "@/lib/local-agent-api";

type ApiKeyRow = {
  id: string;
  key_prefix: string;
  name: string;
  created_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
};

type ApiKeysListResp = { ok?: boolean; items?: ApiKeyRow[] };
type ApiKeyCreateResp = {
  ok?: boolean;
  id?: string;
  api_key?: string;
  key_prefix?: string;
  name?: string;
  created_at?: string;
};

async function copyText(text: string): Promise<boolean> {
  const t = String(text || "");
  if (!t) return false;
  try {
    await navigator.clipboard.writeText(t);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

export function SetupApiKeysPanel() {
  const [items, setItems] = useState<ApiKeyRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState("");
  const [name, setName] = useState("本机 Worker");
  const [lastPlainKey, setLastPlainKey] = useState("");
  const [lastPlainMeta, setLastPlainMeta] = useState("");
  const [applyBusy, setApplyBusy] = useState<"" | "0dte" | "1dte" | "at">("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const r = await apiGet<ApiKeysListResp>("/auth/api-keys", { cacheTtlMs: 0, retries: 0 });
      setItems(Array.isArray(r?.items) ? r.items : []);
    } catch (e) {
      setItems([]);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onCreate = async () => {
    setCreating(true);
    setErr("");
    setLastPlainKey("");
    setLastPlainMeta("");
    try {
      const r = await apiPost<ApiKeyCreateResp>("/auth/api-keys", { name: name.trim() || "default" }, { retries: 0 });
      const plain = String(r?.api_key || "").trim();
      if (!plain) {
        setErr("创建成功但未返回密钥，请重试或检查后端版本。");
        await refresh();
        return;
      }
      setLastPlainKey(plain);
      setLastPlainMeta(`${r?.name || ""} · ${r?.key_prefix || ""}… · ${r?.created_at || ""}`);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const onRevoke = async (id: string) => {
    if (!id || !window.confirm("确定吊销该 Key？本机 Worker 将立即无法使用该密钥。")) return;
    setErr("");
    try {
      await apiDelete(`/auth/api-keys/${encodeURIComponent(id)}`, { retries: 0 });
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const onDeleteRevoked = async (id: string) => {
    if (!id || !window.confirm("确定从列表中永久删除该吊销记录？此操作不可恢复。")) return;
    setErr("");
    try {
      await apiDelete(`/auth/api-keys/${encodeURIComponent(id)}?purge=1`, { retries: 0 });
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const applyToConfig = async (instance: "0dte" | "1dte") => {
    const k = lastPlainKey.trim();
    if (!k) {
      setErr("请先在上方「创建新 Key」并保留本页展示的明文，再写入配置。");
      return;
    }
    const path =
      instance === "0dte" ? "/strategy/qqq-0dte/live-worker-config" : "/strategy/qqq-1dte/live-worker-config";
    setApplyBusy(instance);
    setErr("");
    try {
      await apiPut(path, { api_key: k }, { timeoutMs: 20000, retries: 0 });
      setErr("");
      window.alert(
        instance === "0dte"
          ? "已写入 data/qqq_0dte/live_worker_config.json 的 api_key。若 Worker 已在运行，请重启 Worker 以加载新密钥。"
          : "已写入 data/qqq_1dte/live_worker_config.json 的 api_key。若 Worker 已在运行，请重启 Worker 以加载新密钥。"
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setApplyBusy("");
    }
  };

  const applyToAutoTrader = async () => {
    const k = lastPlainKey.trim();
    if (!k) {
      setErr("请先在上方「创建新 Key」并保留本页展示的明文，再写入配置。");
      return;
    }
    setApplyBusy("at");
    setErr("");
    try {
      await apiPost("/auto-trader/config", { api_key: k }, { timeoutMs: 20000, retries: 0 });
      setErr("");
      window.alert(
        "已写入 api/auto_trader_config.json 的 api_key。若自动交易 Worker/Supervisor 已在运行，请重启以加载新密钥。"
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setApplyBusy("");
    }
  };

  return (
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 space-y-3">
      <div className="text-sm text-slate-200 font-medium">个人 API Key（本机 Worker / 脚本）</div>
      <p className="text-xs text-slate-400 leading-relaxed">
        用于调用需登录的接口（如{" "}
        <code className="text-slate-300">/options/order</code>
        ）。请求头使用{" "}
        <code className="text-slate-300">X-Api-Key: &lt;密钥&gt;</code>
        ，无需从浏览器复制登录 token。密钥仅创建时显示一次，请妥善保存。
      </p>

      {err ? (
        <div className="rounded border border-rose-500/40 bg-rose-950/30 px-2 py-1.5 text-xs text-rose-200">{err}</div>
      ) : null}

      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1 min-w-[180px]">
          <label className="text-xs text-slate-400" htmlFor="api-key-name">
            备注名称
          </label>
          <input
            id="api-key-name"
            className="rounded border border-slate-600 bg-slate-950 px-2 py-1.5 text-sm text-slate-100"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：家里电脑"
          />
        </div>
        <button type="button" className="btn-secondary" disabled={creating} onClick={() => void onCreate()}>
          {creating ? "创建中…" : "创建新 Key"}
        </button>
        <button type="button" className="btn-secondary" disabled={loading} onClick={() => void refresh()}>
          {loading ? "刷新中…" : "刷新列表"}
        </button>
      </div>

      {lastPlainKey ? (
        <div className="rounded border border-amber-500/50 bg-amber-950/25 p-3 space-y-2">
          <div className="text-xs text-amber-200 font-medium">新密钥（仅此一次显示）</div>
          <div className="text-xs text-slate-400">{lastPlainMeta}</div>
          <pre className="break-all rounded bg-slate-950/80 p-2 text-[11px] text-emerald-200/95 whitespace-pre-wrap">
            {lastPlainKey}
          </pre>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary text-xs" onClick={() => void copyText(lastPlainKey)}>
              复制密钥
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              onClick={() =>
                void copyText(`QQQ_LIVE_API_KEY=${lastPlainKey}`)
              }
            >
              复制环境变量行
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              disabled={applyBusy === "0dte"}
              onClick={() => void applyToConfig("0dte")}
            >
              {applyBusy === "0dte" ? "写入中…" : "一键写入 QQQ 0DTE 实盘配置"}
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              disabled={applyBusy === "1dte"}
              onClick={() => void applyToConfig("1dte")}
            >
              {applyBusy === "1dte" ? "写入中…" : "一键写入 QQQ 1DTE 实盘配置"}
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              disabled={applyBusy === "at"}
              onClick={() => void applyToAutoTrader()}
            >
              {applyBusy === "at" ? "写入中…" : "一键写入股票自动交易实盘配置"}
            </button>
          </div>
        </div>
      ) : null}

      <div className="table-shell">
        <table className="min-w-full text-xs">
          <thead className="table-head">
            <tr className="text-left">
              <th className="px-3 py-2">前缀</th>
              <th className="px-3 py-2">名称</th>
              <th className="px-3 py-2">创建时间</th>
              <th className="px-3 py-2">最后使用</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={row.id} className="border-t border-slate-800/90">
                <td className="px-3 py-2 font-mono text-slate-200">{row.key_prefix || "—"}…</td>
                <td className="px-3 py-2 text-slate-300">{row.name}</td>
                <td className="px-3 py-2 text-slate-400">{row.created_at || "—"}</td>
                <td className="px-3 py-2 text-slate-400">{row.last_used_at || "—"}</td>
                <td className="px-3 py-2">
                  {row.revoked_at ? <span className="text-rose-300">已吊销</span> : <span className="text-emerald-300">有效</span>}
                </td>
                <td className="px-3 py-2">
                  {row.revoked_at ? (
                    <button
                      type="button"
                      className="btn-secondary px-2 py-1 text-xs text-slate-200 border-slate-500/40"
                      onClick={() => void onDeleteRevoked(row.id)}
                    >
                      删除
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary px-2 py-1 text-xs text-rose-200 border-rose-500/40"
                      onClick={() => void onRevoke(row.id)}
                    >
                      吊销
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!items.length && !loading ? (
              <tr className="border-t border-slate-800/90">
                <td className="px-3 py-2 text-slate-500" colSpan={6}>
                  暂无 Key。若列表加载失败，请先在本站登录后再试。
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
