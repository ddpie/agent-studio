export type RunSourceKind = "scheduled" | "manual" | "chat";

export interface RunSource {
  kind: RunSourceKind;
  /** Short, translatable label key fragment — callers pass to `t()` with a prefix. */
  label: string;
  /** Icon hint: rendered by the caller using lucide-react. */
  icon: "calendar" | "user" | "chat";
}

/**
 * Classify a run by its session id. Scheduler-triggered runs use the
 * shape `sched-<suffix>-<iso-ts>`; "Run now" uses `sched-<suffix>-manual-<ts>`.
 * Anything else is treated as an interactive chat.
 */
export function inferRunSource(sessionId: string): RunSource {
  if (sessionId.startsWith("sched-")) {
    if (sessionId.includes("-manual-")) {
      return { kind: "manual", label: "manual", icon: "user" };
    }
    return { kind: "scheduled", label: "scheduled", icon: "calendar" };
  }
  return { kind: "chat", label: "chat", icon: "chat" };
}
