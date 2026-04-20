import { useTranslation } from "react-i18next";
import { Calendar, User, MessagesSquare } from "lucide-react";
import TracesTab from "./TracesTab";
import { inferRunSource } from "../../lib/run-source";

interface Props {
  agentId: string;
  initialSessionId?: string | null;
  onSelect?: (sessionId: string) => void;
}

function BadgeIcon({ icon }: { icon: "calendar" | "user" | "chat" }) {
  if (icon === "calendar") return <Calendar className="w-3 h-3" />;
  if (icon === "user") return <User className="w-3 h-3" />;
  return <MessagesSquare className="w-3 h-3" />;
}

export default function RunsTab({ agentId, initialSessionId, onSelect }: Props) {
  const { t } = useTranslation();
  return (
    <TracesTab
      agentId={agentId}
      initialSessionId={initialSessionId}
      titleOverride={t("runs.tab")}
      subtitleOverride={t("runs.subtitle")}
      onSelect={onSelect}
      renderBadge={(sessionId: string) => {
        const src = inferRunSource(sessionId);
        return (
          <>
            <BadgeIcon icon={src.icon} />
            <span>{t(`runs.source.${src.kind}`)}</span>
          </>
        );
      }}
    />
  );
}
