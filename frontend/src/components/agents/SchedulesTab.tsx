import { useCallback, useEffect, useState } from "react";
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
  const { t } = useTranslation();
  const isEdit = !!existing;
  const [name, setName] = useState(existing?.suffix || "");
  const [cron, setCron] = useState(existing?.cron || "cron(0 9 * * ? *)");
  const [prompt, setPrompt] = useState(existing?.prompt || "");
  const [state, setState] = useState<"ENABLED" | "DISABLED">(
    (existing?.state as "ENABLED" | "DISABLED") || "ENABLED"
  );
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1" htmlFor="sched-cron">
              {t("schedules.cronLabel")}
            </label>
            <input
              id="sched-cron"
              data-testid="sched-cron-input"
              type="text"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              className="w-full text-sm font-mono px-2 py-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
            />
            <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
              {t("schedules.cronHint")}
            </div>
            {cronErr && <div className="text-xs text-red-600 dark:text-red-400 mt-1">{cronErr}</div>}
          </div>
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
