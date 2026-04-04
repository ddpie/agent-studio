import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zh from "./locales/zh.json";
import { useUISettings } from "./stores/ui-settings-store";

i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, zh: { translation: zh } },
  lng: useUISettings.getState().language,
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

// Sync language changes from store
useUISettings.subscribe((state) => {
  if (i18n.language !== state.language) {
    i18n.changeLanguage(state.language);
  }
});

export default i18n;
