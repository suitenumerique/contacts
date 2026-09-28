/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_ORIGIN?: string;
  readonly VITE_DEFAULT_LANGUAGE?: string;
  readonly VITE_FORCED_DEFAULT_LANGUAGE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
