import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";

export default function OfflineBanner() {
  const online = useOnlineStatus();
  const { t } = useTranslation();
  const [showBackBriefly, setShowBackBriefly] = useState(false);
  const [wasOffline, setWasOffline] = useState(false);

  useEffect(() => {
    if (!online) {
      setWasOffline(true);
      return;
    }
    if (wasOffline) {
      setShowBackBriefly(true);
      const timer = setTimeout(() => {
        setShowBackBriefly(false);
        setWasOffline(false);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [online, wasOffline]);

  if (!online) {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="offline-banner"
        className="sticky top-0 z-50 w-full bg-red-600 text-white text-center text-sm py-1.5"
      >
        {t("common.offline")}
      </div>
    );
  }

  if (showBackBriefly) {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="online-banner"
        className="sticky top-0 z-50 w-full bg-emerald-600 text-white text-center text-sm py-1.5"
      >
        {t("common.backOnline")}
      </div>
    );
  }

  return null;
}
