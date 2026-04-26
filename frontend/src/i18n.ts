import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { I18n as AmplifyI18n } from "aws-amplify/utils";
import en from "./locales/en.json";
import zh from "./locales/zh.json";
import { useUISettings } from "./stores/ui-settings-store";

// Amplify Authenticator Chinese translations for login/MFA/sign-up screens.
AmplifyI18n.putVocabularies({
  zh: {
    "Sign In": "登录",
    "Sign in": "登录",
    "Sign Up": "注册",
    "Create Account": "创建账户",
    "Create a new account": "创建新账户",
    "Confirm Password": "确认密码",
    "Enter your Email": "输入邮箱",
    "Enter your Password": "输入密码",
    "Enter your Username": "输入用户名",
    "Forgot your password?": "忘记密码了？",
    "Reset your password": "重置密码",
    "Back to Sign In": "返回登录",
    "Send code": "发送验证码",
    "Confirm": "确认",
    "Code": "验证码",
    "New password": "新密码",
    "Username": "用户名",
    "Password": "密码",
    "Email": "邮箱",
    "Phone Number": "电话",
    "Confirmation Code": "确认码",
    "Resend Code": "重发验证码",
    "Submit": "提交",
    "Show password": "显示密码",
    "Hide password": "隐藏密码",
    "Loading": "加载中",
    "Signing in": "登录中",
    "or": "或",
    // MFA / TOTP
    "Setup TOTP": "设置 TOTP",
    "Confirm TOTP Code": "确认 TOTP 验证码",
    "Enter your code": "输入验证码",
    "Multi-Factor Authentication": "多重身份验证",
    "Multi-Factor Authentication Setup": "多重身份验证设置",
    "Authenticator App (TOTP)": "Authenticator 应用（TOTP）",
    "It may take a minute to arrive": "验证码可能需要一分钟送达",
  },
});

const initialLang = useUISettings.getState().language;

i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, zh: { translation: zh } },
  lng: initialLang,
  fallbackLng: "en",
  interpolation: { escapeValue: true },
});

AmplifyI18n.setLanguage(initialLang === "zh" ? "zh" : "en");

useUISettings.subscribe((state) => {
  if (i18n.language !== state.language) {
    i18n.changeLanguage(state.language);
  }
  AmplifyI18n.setLanguage(state.language === "zh" ? "zh" : "en");
});

export default i18n;
