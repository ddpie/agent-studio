import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Plus,
  Trash2,
  Loader2,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Clock,
  Pencil,
  Play,
} from "lucide-react";
import {
  listAgentSchedules,
  createAgentSchedule,
  updateAgentSchedule,
  deleteAgentSchedule,
  listScheduleExecutions,
  runAgentScheduleNow,
  type AgentSchedule,
  type ScheduleExecution,
} from "../../lib/api-client";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { toast } from "../../lib/toast";
import ConfirmDialog from "../ui/ConfirmDialog";
import cronstrue from "cronstrue";
import "cronstrue/locales/zh_CN";
import {
  buildExpression,
  parseExpression,
  nextOccurrences,
  formatLocalShort,
  type ScheduleMode,
  type ScheduleSpec,
} from "../../lib/cron-builder";

interface Props {
  agentId: string;
  onViewTrace?: (sessionId: string) => void;
}

// Accept cron(...) or rate(N units). Mirrors backend validator in
// lambda/crud/schedules.py so users see a friendly message before
// hitting the server.
const CRON_RE = /^cron\([^)]+\)$/;
const RATE_RE = /^rate\(\s*\d+\s+(minute|minutes|hour|hours|day|days)\s*\)$/;
const SUFFIX_RE = /^[a-zA-Z0-9_-]+$/;

function isValidCron(expr: string): boolean {
  return CRON_RE.test(expr) || RATE_RE.test(expr);
}

function formatDuration(ms: number): string {
  if (!ms || ms < 0) return "-";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

export default function SchedulesTab({ agentId, onViewTrace }: Props) {
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspaceStore();
  const role = currentWorkspace?.role || "viewer";
  const canEdit = role === "editor" || role === "admin" || role === "owner";

  const [schedules, setSchedules] = useState<AgentSchedule[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<AgentSchedule | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [pendingDelete, setPendingDelete] = useState<{ name: string; suffix: string } | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await listAgentSchedules(agentId);
      setSchedules(items);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function onDelete(name: string, suffix: string) {
    setPendingDelete({ name, suffix });
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    const { name, suffix } = pendingDelete;
    setPendingDelete(null);
    try {
      await deleteAgentSchedule(agentId, name);
      toast.success(t("schedules.deleted", { name: suffix || name }));
      refresh();
    } catch (err) {
      toast.error(err as Error);
    }
  }

  async function onRunNow(schedule: AgentSchedule) {
    try {
      const res = await runAgentScheduleNow(agentId, schedule.name);
      toast.success(t("schedules.runNowSuccess", { name: schedule.suffix || schedule.name }));
      setExpanded((prev) => ({ ...prev, [schedule.name]: true }));
      if (onViewTrace) {
        // Jump to the trace view immediately so the user sees the
        // "waiting for spans" state. The hook retries until spans land
        // (agent cold-start + OTEL export + CloudWatch ingestion can
        // take 40-60s).
        onViewTrace(res.sessionId);
      }
    } catch (err) {
      toast.error(err as Error);
    }
  }

  function toggleExpand(name: string) {
    setExpanded((prev) => ({ ...prev, [name]: !prev[name] }));
  }

  return (
    <div className="p-4" data-testid="schedules-tab">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold">{t("schedules.title")}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {t("schedules.description")}
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            data-testid="create-schedule-btn"
            className="inline-flex items-center gap-1 rounded bg-blue-600 text-white px-3 py-1 text-xs font-medium hover:bg-blue-700"
          >
            <Plus className="w-3 h-3" />
            {t("schedules.create")}
          </button>
        )}
      </div>

      {error && <div className="text-sm text-red-600 dark:text-red-400">{error.message}</div>}

      {loading && !schedules && (
        <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t("common.loading")}
        </div>
      )}

      {schedules && schedules.length === 0 && !loading && (
        <div className="text-sm text-gray-500 dark:text-gray-400" data-testid="schedules-empty">
          {t("schedules.empty")}
        </div>
      )}

      {schedules && schedules.length > 0 && (
        <table className="w-full text-sm" data-testid="schedules-table">
          <thead className="text-xs text-gray-500 dark:text-gray-400 uppercase">
            <tr>
              <th className="text-left py-2 w-6"></th>
              <th className="text-left py-2">{t("schedules.name")}</th>
              <th className="text-left py-2">{t("schedules.cron")}</th>
              <th className="text-left py-2">{t("schedules.state")}</th>
              <th className="text-left py-2">{t("schedules.createdAt")}</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((s) => (
              <RenderRow
                key={s.name}
                schedule={s}
                agentId={agentId}
                isExpanded={!!expanded[s.name]}
                onToggle={() => toggleExpand(s.name)}
                canEdit={canEdit}
                onDelete={() => onDelete(s.name, s.suffix)}
                onEdit={() => setEditing(s)}
                onRunNow={() => onRunNow(s)}
                onViewTrace={onViewTrace}
              />
            ))}
          </tbody>
        </table>
      )}

      {showCreate && (
        <ScheduleModal
          agentId={agentId}
          onClose={() => setShowCreate(false)}
          onSaved={() => {
            setShowCreate(false);
            refresh();
          }}
        />
      )}

      {editing && (
        <ScheduleModal
          agentId={agentId}
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title={t("schedules.delete")}
        message={t("schedules.confirmDelete", {
          name: pendingDelete ? pendingDelete.suffix || pendingDelete.name : "",
        })}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        danger
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

function RenderRow({
  schedule,
  agentId,
  isExpanded,
  onToggle,
  canEdit,
  onDelete,
  onEdit,
  onRunNow,
  onViewTrace,
}: {
  schedule: AgentSchedule;
  agentId: string;
  isExpanded: boolean;
  onToggle: () => void;
  canEdit: boolean;
  onDelete: () => void;
  onEdit: () => void;
  onRunNow: () => Promise<void>;
  onViewTrace?: (sessionId: string) => void;
}) {
  const [running, setRunning] = useState(false);
  const handleRun = async () => {
    if (running) return;
    setRunning(true);
    try {
      await onRunNow();
    } finally {
      setRunning(false);
    }
  };
  const { t } = useTranslation();
  return (
    <>
      <tr
        className="border-t border-gray-200 dark:border-gray-800"
        data-testid={`schedule-row-${schedule.name}`}
      >
        <td className="py-2">
          <button
            type="button"
            onClick={onToggle}
            className="p-0.5 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
            data-testid={`expand-schedule-${schedule.name}`}
            aria-label={isExpanded ? t("schedules.runs.hide") : t("schedules.runs.show")}
          >
            {isExpanded ? (
              <ChevronDown className="w-4 h-4" />
            ) : (
              <ChevronRight className="w-4 h-4" />
            )}
          </button>
        </td>
        <td className="py-2 font-mono text-xs">{schedule.suffix || schedule.name}</td>
        <td className="py-2 font-mono text-xs">{schedule.cron}</td>
        <td className="py-2 text-xs">{schedule.state || "-"}</td>
        <td className="py-2 text-xs">{schedule.createdAt || "-"}</td>
        <td className="py-2 text-right">
          {canEdit && (
            <div className="inline-flex items-center gap-1">
              <button
                type="button"
                onClick={handleRun}
                disabled={running}
                data-testid={`run-schedule-${schedule.name}`}
                className="p-1 text-gray-500 hover:text-emerald-600 dark:text-gray-400 dark:hover:text-emerald-400 disabled:opacity-50"
                aria-label={t("schedules.runNow")}
                title={t("schedules.runNow")}
              >
                {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              </button>
              <button
                type="button"
                onClick={onEdit}
                data-testid={`edit-schedule-${schedule.name}`}
                className="p-1 text-gray-500 hover:text-blue-600 dark:text-gray-400 dark:hover:text-blue-400"
                aria-label={t("schedules.edit")}
              >
                <Pencil className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={onDelete}
                data-testid={`delete-schedule-${schedule.name}`}
                className="p-1 text-red-500 hover:text-red-700 dark:hover:text-red-400"
                aria-label={t("schedules.delete")}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </td>
      </tr>
      {isExpanded && (
        <tr
          className="bg-gray-50 dark:bg-gray-900/50"
          data-testid={`schedule-runs-${schedule.name}`}
        >
          <td></td>
          <td colSpan={5} className="py-3 pr-4">
            <RecentRuns agentId={agentId} scheduleName={schedule.name} onViewTrace={onViewTrace} />
          </td>
        </tr>
      )}
    </>
  );
}

function RecentRuns({
  agentId,
  scheduleName,
  onViewTrace,
}: {
  agentId: string;
  scheduleName: string;
  onViewTrace?: (sessionId: string) => void;
}) {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<ScheduleExecution[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await listScheduleExecutions(agentId, scheduleName);
      setRuns(items);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [agentId, scheduleName]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-gray-700 dark:text-gray-300">
          {t("schedules.runs.title")}
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 disabled:opacity-50"
          data-testid={`refresh-runs-${scheduleName}`}
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          {t("schedules.runs.refresh")}
        </button>
      </div>

      {loading && !runs && (
        <div className="space-y-1">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-6 rounded bg-gray-200/60 dark:bg-gray-800/60 animate-pulse"
            />
          ))}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400">
          <AlertTriangle className="w-3 h-3" />
          {t("schedules.runs.loadError")}: {error.message}
        </div>
      )}

      {runs && runs.length === 0 && !loading && !error && (
        <div className="text-xs text-gray-500 dark:text-gray-400" data-testid={`runs-empty-${scheduleName}`}>
          {t("schedules.runs.empty")}
        </div>
      )}

      {runs && runs.length > 0 && (
        <table className="w-full text-xs" data-testid={`runs-table-${scheduleName}`}>
          <thead className="text-[10px] text-gray-500 dark:text-gray-400 uppercase">
            <tr>
              <th className="text-left py-1">{t("schedules.runs.time")}</th>
              <th className="text-left py-1">{t("schedules.runs.status")}</th>
              <th className="text-left py-1">{t("schedules.runs.duration")}</th>
              <th className="text-left py-1">{t("schedules.runs.session")}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr
                key={r.sessionId}
                className="border-t border-gray-200 dark:border-gray-800"
                data-testid={`run-row-${r.sessionId}`}
              >
                <td className="py-1 font-mono">{r.scheduledTime || "-"}</td>
                <td className="py-1">
                  <StatusBadge status={r.status} />
                </td>
                <td className="py-1">{formatDuration(r.durationMs)}</td>
                <td className="py-1 font-mono text-[10px] break-all">
                  {onViewTrace ? (
                    <button
                      type="button"
                      onClick={() => onViewTrace(r.sessionId)}
                      data-testid={`run-view-trace-${r.sessionId}`}
                      title={t("schedules.viewTrace")}
                      className="text-blue-600 hover:underline dark:text-blue-400"
                    >
                      {r.sessionId}
                    </button>
                  ) : (
                    <span className="text-gray-500 dark:text-gray-400">{r.sessionId}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: ScheduleExecution["status"] }) {
  const { t } = useTranslation();
  if (status === "failure") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300">
        <XCircle className="w-3 h-3" />
        {t("schedules.runs.statusFailure")}
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-800 dark:bg-yellow-950/40 dark:text-yellow-300">
        <Clock className="w-3 h-3" />
        {t("schedules.runs.statusRunning")}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-300">
      <CheckCircle2 className="w-3 h-3" />
      {t("schedules.runs.statusSuccess")}
    </span>
  );
}

function ScheduleModal({
  agentId,
  existing,
  onClose,
  onSaved,
}: {
  agentId: string;
  existing?: AgentSchedule;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t, i18n } = useTranslation();
  const isEdit = !!existing;
  const [name, setName] = useState(existing?.suffix || "");
  const initialSpec = useMemo<ScheduleSpec>(
    () => (existing?.cron ? parseExpression(existing.cron) : { mode: "daily", minute: 0, hour: 9 }),
    [existing?.cron],
  );
  const [spec, setSpec] = useState<ScheduleSpec>(initialSpec);
  const cron = useMemo(() => buildExpression(spec), [spec]);
  const [prompt, setPrompt] = useState(existing?.prompt || "");
  const [state, setState] = useState<"ENABLED" | "DISABLED">(
    (existing?.state as "ENABLED" | "DISABLED") || "ENABLED"
  );
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const nextRuns = useMemo(() => {
    if (!cron || !isValidCron(cron)) return [];
    try {
      return nextOccurrences(cron, 5);
    } catch {
      return [];
    }
  }, [cron]);

  const nameErr = !isEdit && name && !SUFFIX_RE.test(name)
    ? t("schedules.errors.nameFormat")
    : null;
  const cronErr = cron && !isValidCron(cron) ? t("schedules.errors.cronFormat") : null;
  const promptErr = prompt.length > 4000 ? t("schedules.errors.promptLength") : null;

  const canSubmit =
    !submitting &&
    !!name &&
    !nameErr &&
    !!cron &&
    !cronErr &&
    !!prompt.trim() &&
    !promptErr;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setErr(null);
    try {
      if (isEdit && existing) {
        // Only send changed fields so the server keeps untouched
        // target payload data intact.
        const patch: { cron?: string; prompt?: string; state?: "ENABLED" | "DISABLED" } = {};
        if (cron !== existing.cron) patch.cron = cron;
        if (prompt !== (existing.prompt || "")) patch.prompt = prompt;
        if (state !== (existing.state || "ENABLED")) patch.state = state;
        if (Object.keys(patch).length === 0) {
          onSaved();
          return;
        }
        await updateAgentSchedule(agentId, existing.name, patch);
        toast.success(t("schedules.updated", { name }));
      } else {
        await createAgentSchedule(agentId, { name, cron, prompt });
        toast.success(t("schedules.created", { name }));
      }
      onSaved();
    } catch (e2) {
      const msg = (e2 as Error).message || (isEdit
        ? t("schedules.updateFailed")
        : "Failed to create schedule");
      setErr(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid={isEdit ? "edit-schedule-modal" : "create-schedule-modal"}
    >
      <div
        className="bg-white dark:bg-gray-900 rounded-lg p-5 w-[560px] max-w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold mb-3">
          {isEdit ? t("schedules.editModalTitle") : t("schedules.modalTitle")}
        </h3>
        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1" htmlFor="sched-name">
              {t("schedules.nameLabel")}
            </label>
            <input
              id="sched-name"
              data-testid="sched-name-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="daily-summary"
              disabled={isEdit}
              className="w-full text-sm px-2 py-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100 disabled:opacity-60 disabled:cursor-not-allowed"
              maxLength={32}
            />
            <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
              {isEdit
                ? t("schedules.nameCannotChange")
                : t("schedules.nameHint", { prefix: `agent-studio-${agentId}-` })}
            </div>
            {nameErr && <div className="text-xs text-red-600 dark:text-red-400 mt-1">{nameErr}</div>}
          </div>
          <ScheduleBuilder
            spec={spec}
            onChange={setSpec}
            cron={cron}
            nextRuns={nextRuns}
            cronErr={cronErr}
            locale={i18n.language}
          />
          {cronErr && <div className="text-xs text-red-600 dark:text-red-400 mt-1">{cronErr}</div>}
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1" htmlFor="sched-prompt">
              {t("schedules.promptLabel")}
            </label>
            <textarea
              id="sched-prompt"
              data-testid="sched-prompt-input"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              className="w-full text-sm px-2 py-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
              maxLength={4000}
            />
            {promptErr && <div className="text-xs text-red-600 dark:text-red-400 mt-1">{promptErr}</div>}
          </div>

          {isEdit && (
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1" htmlFor="sched-state">
                {t("schedules.state")}
              </label>
              <select
                id="sched-state"
                data-testid="sched-state-input"
                value={state}
                onChange={(e) => setState(e.target.value as "ENABLED" | "DISABLED")}
                className="w-full text-sm px-2 py-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
              >
                <option value="ENABLED">{t("schedules.enabled")}</option>
                <option value="DISABLED">{t("schedules.disabled")}</option>
              </select>
            </div>
          )}

          {err && (
            <div className="flex items-start gap-2 p-2 rounded bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900">
              <AlertTriangle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
              <div className="text-xs text-red-800 dark:text-red-200 break-all">{err}</div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs rounded bg-gray-200 dark:bg-gray-800 hover:bg-gray-300 dark:hover:bg-gray-700"
              data-testid="sched-cancel"
            >
              {t("common.cancel")}
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              data-testid="sched-submit"
              className="px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-1">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  {t("common.loading")}
                </span>
              ) : isEdit ? (
                t("common.save")
              ) : (
                t("common.create")
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}


// ─── Schedule Builder ────────────────────────────────────────────────

const MODE_OPTIONS: ScheduleMode[] = ["minutes", "hourly", "daily", "weekly", "monthly", "advanced"];

function ScheduleBuilder({
  spec,
  onChange,
  cron,
  nextRuns,
  cronErr,
  locale,
}: {
  spec: ScheduleSpec;
  onChange: (s: ScheduleSpec) => void;
  cron: string;
  nextRuns: Date[];
  cronErr: string | null;
  locale: string;
}) {
  const { t } = useTranslation();
  const zh = locale.startsWith("zh");

  const humanReadable = useMemo(() => {
    if (!cron || cronErr) return "";
    // rate() is trivial — describe it ourselves.
    const rate = cron.match(/^rate\(\s*(\d+)\s+(minute|minutes|hour|hours|day|days)\s*\)$/i);
    if (rate) {
      const n = rate[1];
      const u = rate[2].toLowerCase();
      if (zh) {
        const zu = u.startsWith("hour") ? "小时" : u.startsWith("day") ? "天" : "分钟";
        return `每 ${n} ${zu}`;
      }
      return `Every ${n} ${u}`;
    }
    try {
      // cronstrue speaks 5-field + Quartz; AWS 6-field is compatible when
      // we feed it as Quartz by keeping the `?` and dropping the trailing
      // year token. `throwExceptionOnParseError: false` returns an error
      // string inline instead of throwing, which we then suppress.
      const body = cron.replace(/^cron\(|\)$/g, "").trim();
      const parts = body.split(/\s+/);
      const quartz = parts.length === 6 ? parts.slice(0, 5).join(" ") : body;
      const desc = cronstrue.toString(quartz, {
        locale: zh ? "zh_CN" : "en",
        use24HourTimeFormat: true,
        throwExceptionOnParseError: false,
      });
      if (desc && !/error|expression/i.test(desc.split(":")[0])) return desc;
      return "";
    } catch {
      return "";
    }
  }, [cron, cronErr, zh]);

  return (
    <div>
      <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1.5">
        {t("schedules.cronLabel")}
      </label>

      {/* Mode tabs */}
      <div className="flex flex-wrap gap-1 mb-2" role="tablist">
        {MODE_OPTIONS.map((m) => (
          <button
            key={m}
            type="button"
            data-testid={`sched-mode-${m}`}
            onClick={() => onChange(switchMode(m, spec))}
            className={`text-[11px] px-2.5 py-1 rounded-md border transition-colors ${
              spec.mode === m
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            }`}
          >
            {t(`schedules.modes.${m}`)}
          </button>
        ))}
      </div>

      {/* Mode-specific controls */}
      <div className="rounded-md border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50 p-3">
        {spec.mode === "minutes" && (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-gray-600 dark:text-gray-300">{t("schedules.builder.every")}</span>
            <input
              type="number"
              min={1}
              max={999}
              value={spec.rateValue ?? 5}
              onChange={(e) => onChange({ ...spec, rateValue: Number(e.target.value) || 1 })}
              data-testid="sched-rate-value"
              className="w-16 px-2 py-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
            />
            <select
              value={spec.rateUnit ?? "minutes"}
              onChange={(e) => onChange({ ...spec, rateUnit: e.target.value as ScheduleSpec["rateUnit"] })}
              data-testid="sched-rate-unit"
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
            >
              <option value="minutes">{t("schedules.builder.minutes")}</option>
              <option value="hours">{t("schedules.builder.hours")}</option>
              <option value="days">{t("schedules.builder.days")}</option>
            </select>
          </div>
        )}

        {spec.mode === "hourly" && (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-gray-600 dark:text-gray-300">{t("schedules.builder.atMinute")}</span>
            <NumberInput
              value={spec.minute ?? 0}
              min={0}
              max={59}
              onChange={(v) => onChange({ ...spec, minute: v })}
              testId="sched-minute"
            />
            <span className="text-gray-500 dark:text-gray-400">{t("schedules.builder.pastTheHour")}</span>
          </div>
        )}

        {spec.mode === "daily" && (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-gray-600 dark:text-gray-300">{t("schedules.builder.at")}</span>
            <TimeInput
              hour={spec.hour ?? 9}
              minute={spec.minute ?? 0}
              onChange={(h, m) => onChange({ ...spec, hour: h, minute: m })}
            />
            <span className="text-gray-500 dark:text-gray-400">UTC</span>
          </div>
        )}

        {spec.mode === "weekly" && (
          <div className="space-y-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-gray-600 dark:text-gray-300">{t("schedules.builder.at")}</span>
              <TimeInput
                hour={spec.hour ?? 9}
                minute={spec.minute ?? 0}
                onChange={(h, m) => onChange({ ...spec, hour: h, minute: m })}
              />
              <span className="text-gray-500 dark:text-gray-400">UTC</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {[0, 1, 2, 3, 4, 5, 6].map((d) => {
                const active = (spec.weekdays ?? [1]).includes(d);
                return (
                  <button
                    key={d}
                    type="button"
                    data-testid={`sched-weekday-${d}`}
                    onClick={() => {
                      const cur = new Set(spec.weekdays ?? [1]);
                      if (cur.has(d)) cur.delete(d);
                      else cur.add(d);
                      onChange({ ...spec, weekdays: [...cur].sort((a, b) => a - b) });
                    }}
                    className={`w-9 h-7 text-[11px] rounded border ${
                      active
                        ? "bg-blue-600 text-white border-blue-600"
                        : "bg-white dark:bg-gray-950 border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
                    }`}
                  >
                    {t(`schedules.builder.weekdayShort.${d}`)}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {spec.mode === "monthly" && (
          <div className="flex items-center gap-2 text-xs flex-wrap">
            <span className="text-gray-600 dark:text-gray-300">{t("schedules.builder.onDay")}</span>
            <NumberInput
              value={spec.dayOfMonth ?? 1}
              min={1}
              max={31}
              onChange={(v) => onChange({ ...spec, dayOfMonth: v })}
              testId="sched-dom"
            />
            <span className="text-gray-600 dark:text-gray-300">{t("schedules.builder.at")}</span>
            <TimeInput
              hour={spec.hour ?? 9}
              minute={spec.minute ?? 0}
              onChange={(h, m) => onChange({ ...spec, hour: h, minute: m })}
            />
            <span className="text-gray-500 dark:text-gray-400">UTC</span>
          </div>
        )}

        {spec.mode === "advanced" && (
          <div className="space-y-1">
            <input
              type="text"
              value={spec.raw ?? ""}
              onChange={(e) => onChange({ mode: "advanced", raw: e.target.value })}
              data-testid="sched-raw-input"
              placeholder="cron(0 9 * * ? *) or rate(5 minutes)"
              className="w-full text-xs font-mono px-2 py-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
            />
            <div className="text-[11px] text-gray-500 dark:text-gray-400">
              {t("schedules.cronHint")}
            </div>
          </div>
        )}
      </div>

      {/* Preview strip: expression + human-readable + next 5 runs */}
      <div className="mt-2 space-y-1.5">
        <div className="flex items-center gap-2 text-[11px]">
          <span className="text-gray-500 dark:text-gray-400 w-20 shrink-0">{t("schedules.builder.expression")}</span>
          <code className="flex-1 px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono text-gray-800 dark:text-gray-200 break-all">
            {cron || "—"}
          </code>
        </div>
        {humanReadable && !cronErr && (
          <div className="flex items-start gap-2 text-[11px]">
            <span className="text-gray-500 dark:text-gray-400 w-20 shrink-0 pt-0.5">{t("schedules.builder.inPlainEnglish")}</span>
            <span className="flex-1 text-gray-700 dark:text-gray-300" data-testid="sched-human">{humanReadable}</span>
          </div>
        )}
        {!cronErr && nextRuns.length > 0 && (
          <div className="flex items-start gap-2 text-[11px]">
            <span className="text-gray-500 dark:text-gray-400 w-20 shrink-0 pt-0.5">{t("schedules.builder.nextRuns")}</span>
            <ul className="flex-1 space-y-0.5 text-gray-700 dark:text-gray-300" data-testid="sched-next-runs">
              {nextRuns.map((d, i) => (
                <li key={i} className="font-mono">
                  {formatLocalShort(d)} <span className="text-gray-400 dark:text-gray-500">({localTzLabel()})</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function switchMode(mode: ScheduleMode, prev: ScheduleSpec): ScheduleSpec {
  // Preserve hour/minute across compatible modes so switching preset
  // doesn't throw away user's typed time.
  const h = prev.hour ?? 9;
  const m = prev.minute ?? 0;
  switch (mode) {
    case "minutes":
      return { mode, rateValue: prev.rateValue ?? 5, rateUnit: prev.rateUnit ?? "minutes" };
    case "hourly":
      return { mode, minute: m };
    case "daily":
      return { mode, minute: m, hour: h };
    case "weekly":
      return { mode, minute: m, hour: h, weekdays: prev.weekdays ?? [1] };
    case "monthly":
      return { mode, minute: m, hour: h, dayOfMonth: prev.dayOfMonth ?? 1 };
    case "advanced":
      return { mode, raw: prev.raw ?? "" };
  }
}

function NumberInput({
  value, min, max, onChange, testId,
}: { value: number; min: number; max: number; onChange: (v: number) => void; testId?: string }) {
  return (
    <input
      type="number"
      min={min}
      max={max}
      value={value}
      onChange={(e) => {
        const v = Number(e.target.value);
        if (Number.isNaN(v)) return;
        onChange(Math.max(min, Math.min(max, v)));
      }}
      data-testid={testId}
      className="w-14 px-2 py-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
    />
  );
}

function TimeInput({
  hour, minute, onChange,
}: { hour: number; minute: number; onChange: (h: number, m: number) => void }) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    <input
      type="time"
      value={`${pad(hour)}:${pad(minute)}`}
      onChange={(e) => {
        const [h, m] = e.target.value.split(":").map((x) => Number(x));
        if (Number.isNaN(h) || Number.isNaN(m)) return;
        onChange(h, m);
      }}
      data-testid="sched-time-input"
      className="px-2 py-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100 font-mono"
    />
  );
}

function localTzLabel(): string {
  const offset = -new Date().getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return m ? `UTC${sign}${h}:${String(m).padStart(2, "0")}` : `UTC${sign}${h}`;
}
