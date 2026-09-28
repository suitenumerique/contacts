import type { Config } from "./gen/models";

/** GET /config/, with the loosely typed JSON settings narrowed. */
export type ApiConfig = Omit<Config, "FRONTEND_FEEDBACK_ITEMS" | "theme_customization"> & {
  FRONTEND_FEEDBACK_ITEMS?: Record<string, { url: string }>;
  theme_customization?: ThemeCustomization;
};

export interface ThemeCustomization {
  footer?: LocalizedRecord;
  [key: string]: LocalizedRecord | unknown;
}

export interface LocalizedRecord {
  default?: Record<string, unknown>;
  [languageCode: string]: Record<string, unknown> | undefined;
}
