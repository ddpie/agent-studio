import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  fetchUserAttributes,
  updateUserAttributes,
  updatePassword,
  signOut,
} from "aws-amplify/auth";
import { User, KeyRound, LogOut, Mail, Loader2 } from "lucide-react";
import { toast } from "../../lib/toast";
import ConfirmDialog from "../ui/ConfirmDialog";
import { clearUserScopedLocalData } from "../../lib/api-client";

/**
 * Password policy mirrors infra/lib/constructs/auth.ts:
 *   minLength: 8, requireLowercase, requireUppercase, requireDigits.
 */
function validateNewPassword(pw: string): string | null {
  if (pw.length < 8) return "tooShort";
  if (!/[a-z]/.test(pw)) return "needsLower";
  if (!/[A-Z]/.test(pw)) return "needsUpper";
  if (!/\d/.test(pw)) return "needsDigit";
  return null;
}

export default function AccountSettingsTab() {
  const { t } = useTranslation();

  // Profile
  const [email, setEmail] = useState<string>("");
  const [initialName, setInitialName] = useState<string>("");
  const [name, setName] = useState<string>("");
  const [loadingProfile, setLoadingProfile] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);

  // Password
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);

  // Global sign out
  const [signOutAllOpen, setSignOutAllOpen] = useState(false);
  const [signingOutAll, setSigningOutAll] = useState(false);

  const loadAttributes = useCallback(async () => {
    setLoadingProfile(true);
    try {
      const attrs = await fetchUserAttributes();
      setEmail(attrs.email || "");
      setInitialName(attrs.name || "");
      setName(attrs.name || "");
    } catch (err) {
      toast.error(err instanceof Error ? err : t("settings.account.loadFailed"));
    } finally {
      setLoadingProfile(false);
    }
  }, [t]);

  useEffect(() => {
    loadAttributes();
  }, [loadAttributes]);

  const profileDirty = name.trim() !== initialName;

  const handleSaveProfile = async () => {
    if (!profileDirty) return;
    setSavingProfile(true);
    try {
      await updateUserAttributes({ userAttributes: { name: name.trim() } });
      setInitialName(name.trim());
      toast.success(t("settings.account.profileSaved"));
    } catch (err) {
      toast.error(err instanceof Error ? err : t("settings.account.profileSaveFailed"));
    } finally {
      setSavingProfile(false);
    }
  };

  const clearPasswordFields = () => {
    setOldPassword("");
    setNewPassword("");
    setConfirmPassword("");
  };

  const handleChangePassword = async () => {
    if (!oldPassword || !newPassword || !confirmPassword) {
      toast.error(t("settings.account.passwordAllRequired"));
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error(t("settings.account.passwordMismatch"));
      return;
    }
    const policyError = validateNewPassword(newPassword);
    if (policyError) {
      toast.error(t(`settings.account.policy.${policyError}`));
      return;
    }
    setChangingPassword(true);
    try {
      await updatePassword({ oldPassword, newPassword });
      clearPasswordFields();
      toast.success(t("settings.account.passwordChanged"));
    } catch (err) {
      // Never log password values. Only the (non-sensitive) error message is surfaced.
      toast.error(err instanceof Error ? err : t("settings.account.passwordChangeFailed"));
      // Clear the old password field so a retry requires re-typing.
      setOldPassword("");
    } finally {
      setChangingPassword(false);
    }
  };

  const handleSignOutAll = async () => {
    setSignOutAllOpen(false);
    setSigningOutAll(true);
    try {
      // Wipe chat history + drafts BEFORE Amplify kills the session. The
      // Authenticator-wrapped signOut in App.tsx does this automatically,
      // but "Sign out of all devices" bypasses that wrapper by calling the
      // bare Amplify API — so re-run the cleanup inline to avoid leaving
      // tool code / agent prompts readable on a shared browser.
      await clearUserScopedLocalData();
      // Amplify v6: `global: true` revokes all of this user's refresh tokens across devices.
      await signOut({ global: true });
      toast.success(t("settings.account.signedOutAll"));
      // The Authenticator wrapper in App.tsx will re-render back to sign-in automatically.
    } catch (err) {
      toast.error(err instanceof Error ? err : t("settings.account.signOutAllFailed"));
    } finally {
      setSigningOutAll(false);
    }
  };

  const inputCls =
    "w-full px-2.5 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-60";

  return (
    <div className="space-y-4">
      {/* Profile */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <User className="w-3.5 h-3.5" /> {t("settings.account.profile")}
        </h3>

        {loadingProfile ? (
          <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t("common.loading")}
          </div>
        ) : (
          <div className="space-y-3">
            {/* Email (read-only) */}
            <div>
              <label className="text-[10px] font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1 mb-1">
                <Mail className="w-3 h-3" /> {t("settings.account.email")}
              </label>
              <input
                type="email"
                value={email}
                readOnly
                disabled
                className={`${inputCls} font-mono`}
              />
              <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-1">{t("settings.account.emailReadonly")}</p>
            </div>

            {/* Display name */}
            <div>
              <label className="text-[10px] font-medium text-gray-500 dark:text-gray-400 mb-1 block">
                {t("settings.account.displayName")}
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("settings.account.displayNamePlaceholder")}
                maxLength={120}
                className={inputCls}
              />
              <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-1">{t("settings.account.displayNameHint")}</p>
            </div>

            <div className="flex justify-end">
              <button
                onClick={handleSaveProfile}
                disabled={!profileDirty || savingProfile}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {savingProfile && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {t("common.save")}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Change password */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <KeyRound className="w-3.5 h-3.5" /> {t("settings.account.changePassword")}
        </h3>

        <div className="space-y-3">
          <div>
            <label className="text-[10px] font-medium text-gray-500 dark:text-gray-400 mb-1 block">
              {t("settings.account.currentPassword")}
            </label>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              autoComplete="current-password"
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-[10px] font-medium text-gray-500 dark:text-gray-400 mb-1 block">
              {t("settings.account.newPassword")}
            </label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              className={inputCls}
            />
            <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-1">{t("settings.account.passwordRules")}</p>
          </div>
          <div>
            <label className="text-[10px] font-medium text-gray-500 dark:text-gray-400 mb-1 block">
              {t("settings.account.confirmPassword")}
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className={inputCls}
            />
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleChangePassword}
              disabled={changingPassword || !oldPassword || !newPassword || !confirmPassword}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {changingPassword && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {t("settings.account.changePassword")}
            </button>
          </div>
        </div>
      </section>

      {/* Sessions */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <LogOut className="w-3.5 h-3.5" /> {t("settings.account.sessions")}
        </h3>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          {t("settings.account.signOutAllDesc")}
        </p>
        <button
          onClick={() => setSignOutAllOpen(true)}
          disabled={signingOutAll}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 dark:border-red-800 rounded-lg text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
        >
          {signingOutAll ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <LogOut className="w-3.5 h-3.5" />
          )}
          {t("settings.account.signOutAll")}
        </button>
      </section>

      <ConfirmDialog
        open={signOutAllOpen}
        title={t("settings.account.signOutAll")}
        message={t("settings.account.signOutAllConfirm")}
        confirmLabel={t("settings.account.signOutAll")}
        onConfirm={handleSignOutAll}
        onCancel={() => setSignOutAllOpen(false)}
        danger
      />
    </div>
  );
}
