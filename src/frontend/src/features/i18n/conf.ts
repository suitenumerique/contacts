// Django language codes are lowercase ("fr-fr", not "fr-FR").
export const LANGUAGES = [
  { label: "Français", value: "fr-fr", shortLabel: "FR" },
  { label: "English", value: "en-us", shortLabel: "EN" },
  { label: "Nederlands", value: "nl-nl", shortLabel: "NL" },
];
export const LANGUAGES_ALLOWED = LANGUAGES.map((language) => language.value);
export const LANGUAGE_LOCAL_STORAGE = "main-language";
export const BASE_LANGUAGE = import.meta.env.VITE_DEFAULT_LANGUAGE || "fr-fr";
export const IS_LANGUAGE_FORCED = import.meta.env.VITE_FORCED_DEFAULT_LANGUAGE === "true";
