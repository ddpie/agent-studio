import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Loader2,
  MessageSquare,
  X,
} from "lucide-react";
import { useChannelStore } from "../../stores/channel-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { toast } from "../../lib/toast";

interface ChannelWizardProps {
  onClose: () => void;
  onCreated: () => void;
}

type Platform = "feishu" | "dingtalk" | "slack";
type TriggerMode = "mention" | "all" | "keyword";

export default function ChannelWizard({ onClose, onCreated }: ChannelWizardProps) {
  const { t } = useTranslation();
  const { createChannel } = useChannelStore();
  const { agents } = useAgentListStore();

  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5>(1);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [channelName, setChannelName] = useState("");
  const [defaultAgentId, setDefaultAgentId] = useState("");
  const [triggerMode, setTriggerMode] = useState<TriggerMode>("mention");
  const [historyTurns, setHistoryTurns] = useState(10);
  const [botLanguage, setBotLanguage] = useState("zh");
  const [verifyStatus, setVerifyStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [verifyError, setVerifyError] = useState("");

  const canNext = (): boolean => {
    switch (step) {
      case 1: return !!platform;
      case 2: return true;
      case 3: return !!appId.trim() && !!appSecret.trim();
      case 4: return !!defaultAgentId;
      case 5: return verifyStatus === "success";
      default: return false;
    }
  };

  const handleNext = () => {
    if (step < 5) setStep((step + 1) as 1 | 2 | 3 | 4 | 5);
  };

  const handleBack = () => {
    if (step > 1) setStep((step - 1) as 1 | 2 | 3 | 4 | 5);
  };

  const handleVerify = async () => {
    setVerifyStatus("loading");
    setVerifyError("");
    try {
      await createChannel({
        channelType: platform!,
        channelName: channelName || `${platform}-bot`,
        defaultAgentId,
        triggerMode,
        platformConfig: {
          appId,
          historyTurns,
          language: botLanguage,
        },
        appSecret,
        routingRules: [],
      });
      setVerifyStatus("success");
    } catch (err) {
      setVerifyStatus("error");
      setVerifyError(err instanceof Error ? err.message : "Connection failed");
    }
  };

  const handleComplete = () => {
    toast.success(t("channels.verify_success"));
    onCreated();
  };

  const stepLabels = [
    t("channels.wizard_step1"),
    t("channels.wizard_step2"),
    t("channels.wizard_step3"),
    t("channels.wizard_step4"),
    t("channels.wizard_step5"),
  ];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide flex items-center gap-1.5">
          <MessageSquare className="w-3.5 h-3.5" />
          {t("channels.connect")}
        </h3>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-1">
        {stepLabels.map((label, i) => (
          <div key={i} className="flex items-center gap-1">
            <div
              className={`flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-medium ${
                i + 1 === step
                  ? "bg-blue-600 text-white"
                  : i + 1 < step
                  ? "bg-green-500 text-white"
                  : "bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
              }`}
            >
              {i + 1 < step ? <Check className="w-3 h-3" /> : i + 1}
            </div>
            <span className={`text-[10px] ${i + 1 === step ? "text-gray-700 dark:text-gray-200 font-medium" : "text-gray-400 dark:text-gray-500"}`}>
              {label}
            </span>
            {i < stepLabels.length - 1 && (
              <div className="w-4 h-px bg-gray-300 dark:bg-gray-600 mx-1" />
            )}
          </div>
        ))}
      </div>

      {/* Step content */}
      <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 min-h-[200px]">
        {/* Step 1: Select Platform */}
        {step === 1 && (
          <div className="space-y-3">
            <p className="text-xs text-gray-600 dark:text-gray-400">
              {t("channels.wizard_step1")}
            </p>
            <div className="grid grid-cols-3 gap-3">
              {(["feishu", "dingtalk", "slack"] as Platform[]).map((p) => {
                const isAvailable = p === "feishu";
                return (
                <button
                  key={p}
                  disabled={!isAvailable}
                  onClick={() => {
                    if (!isAvailable) return;
                    setPlatform(p);
                    setChannelName(`${p}-bot`);
                  }}
                  className={`flex flex-col items-center gap-2 p-4 rounded-lg border transition-colors ${
                    !isAvailable
                      ? "border-gray-200 dark:border-gray-700 opacity-50 cursor-not-allowed"
                      : platform === p
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                        : "border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`}
                >
                  <MessageSquare className={`w-6 h-6 ${platform === p ? "text-blue-600 dark:text-blue-400" : "text-gray-500 dark:text-gray-400"}`} />
                  <span className={`text-xs font-medium ${platform === p ? "text-blue-700 dark:text-blue-300" : "text-gray-700 dark:text-gray-300"}`}>
                    {t(`channels.${p}`)}
                  </span>
                  {!isAvailable && <span className="text-[10px] text-gray-400 dark:text-gray-500">Coming soon</span>}
                </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Step 2: Setup Guide */}
        {step === 2 && (
          <div className="space-y-3">
            <p className="text-xs text-gray-600 dark:text-gray-400 mb-2">
              {t("channels.wizard_step2")}
            </p>
            {platform === "feishu" && (
              <div className="space-y-2">
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    1. {t("channels.guide_feishu_step1")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_feishu_step1_detail")}
                  </div>
                </details>
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    2. {t("channels.guide_feishu_step2")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_feishu_step2_detail")}
                  </div>
                </details>
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    3. {t("channels.guide_feishu_step3")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_feishu_step3_detail")}
                  </div>
                </details>
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    4. {t("channels.guide_feishu_step4")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_feishu_step4_detail")}
                  </div>
                </details>
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    5. {t("channels.guide_feishu_step5")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_feishu_step5_detail")}
                  </div>
                </details>
              </div>
            )}
            {platform === "dingtalk" && (
              <div className="space-y-2">
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    1. {t("channels.guide_dingtalk_step1")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_dingtalk_step1_detail")}
                  </div>
                </details>
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    2. {t("channels.guide_dingtalk_step2")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_dingtalk_step2_detail")}
                  </div>
                </details>
              </div>
            )}
            {platform === "slack" && (
              <div className="space-y-2">
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    1. {t("channels.guide_slack_step1")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_slack_step1_detail")}
                  </div>
                </details>
                <details className="group border border-gray-200 dark:border-gray-700 rounded-lg">
                  <summary className="px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg">
                    2. {t("channels.guide_slack_step2")}
                  </summary>
                  <div className="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                    {t("channels.guide_slack_step2_detail")}
                  </div>
                </details>
              </div>
            )}
          </div>
        )}

        {/* Step 3: Credentials */}
        {step === 3 && (
          <div className="space-y-3">
            <p className="text-xs text-gray-600 dark:text-gray-400">
              {t("channels.wizard_step3")}
            </p>
            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                {t("channels.app_id")}
              </label>
              <input
                type="text"
                value={appId}
                onChange={(e) => setAppId(e.target.value)}
                placeholder="cli_xxxxxxxx"
                className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                {t("channels.app_secret")}
              </label>
              <input
                type="password"
                value={appSecret}
                onChange={(e) => setAppSecret(e.target.value)}
                placeholder="xxxxxxxxxxxxxxxx"
                className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        )}

        {/* Step 4: Configure */}
        {step === 4 && (
          <div className="space-y-4">
            <p className="text-xs text-gray-600 dark:text-gray-400">
              {t("channels.wizard_step4")}
            </p>
            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                {t("channels.default_agent")}
              </label>
              <select
                value={defaultAgentId}
                onChange={(e) => setDefaultAgentId(e.target.value)}
                className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200"
              >
                <option value="">—</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.displayName || a.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                {t("channels.trigger_mode")}
              </label>
              <div className="flex gap-2">
                {(["mention", "all", "keyword"] as TriggerMode[]).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => setTriggerMode(mode)}
                    className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                      triggerMode === mode
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                        : "border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
                    }`}
                  >
                    {t(`channels.trigger_${mode}`)}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                {t("channels.history_turns")}: {historyTurns}
              </label>
              <input
                type="range"
                min={0}
                max={30}
                value={historyTurns}
                onChange={(e) => setHistoryTurns(Number(e.target.value))}
                className="w-full"
              />
            </div>

            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                {t("channels.language")}
              </label>
              <div className="flex gap-2">
                {[
                  { id: "zh", label: "中文" },
                  { id: "en", label: "English" },
                ].map(({ id, label }) => (
                  <button
                    key={id}
                    onClick={() => setBotLanguage(id)}
                    className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                      botLanguage === id
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                        : "border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Step 5: Verify */}
        {step === 5 && (
          <div className="space-y-4">
            <p className="text-xs text-gray-600 dark:text-gray-400">
              {t("channels.wizard_step5")}
            </p>

            {verifyStatus === "idle" && (
              <button
                onClick={handleVerify}
                className="flex items-center gap-1.5 px-4 py-2 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
              >
                {t("channels.complete_setup")}
              </button>
            )}

            {verifyStatus === "loading" && (
              <div className="flex items-center gap-2 text-xs text-blue-600 dark:text-blue-400">
                <Loader2 className="w-4 h-4 animate-spin" />
                {t("channels.verify_connecting")}
              </div>
            )}

            {verifyStatus === "success" && (
              <div className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400">
                <Check className="w-4 h-4" />
                {t("channels.verify_success")}
              </div>
            )}

            {verifyStatus === "error" && (
              <div className="space-y-2">
                <div className="text-xs text-red-600 dark:text-red-400">
                  {t("channels.verify_failed")}
                </div>
                {verifyError && (
                  <div className="text-[11px] text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900 rounded p-2 font-mono">
                    {verifyError}
                  </div>
                )}
                <button
                  onClick={handleVerify}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  {t("channels.complete_setup")}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between">
        <button
          onClick={step === 1 ? onClose : handleBack}
          className="flex items-center gap-1 px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          {t("common.back")}
        </button>
        {step < 5 ? (
          <button
            onClick={handleNext}
            disabled={!canNext()}
            className="flex items-center gap-1 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {stepLabels[step] || t("common.confirm")}
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        ) : (
          verifyStatus === "success" && (
            <button
              onClick={handleComplete}
              className="flex items-center gap-1 px-3 py-1.5 text-xs bg-green-600 hover:bg-green-700 text-white rounded-lg"
            >
              <Check className="w-3.5 h-3.5" />
              {t("channels.complete_setup")}
            </button>
          )
        )}
      </div>
    </div>
  );
}
