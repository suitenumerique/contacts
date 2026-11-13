import { Spinner } from "@gouvfr-lasuite/ui-components";
import { useApiConfig } from "./useApiConfig";
import { ApiConfig } from "@/features/api/types";
import { createContext, useContext, useEffect } from "react";
import { useAppContext } from "@/features/app/AppContext";
import { AppErrorScreen } from "@/features/errors/AppErrorScreen";

export interface ConfigContextType {
  config: ApiConfig;
}

export const ConfigContext = createContext<ConfigContextType | undefined>(undefined);

export const useConfig = () => {
  const context = useContext(ConfigContext);
  if (!context) {
    throw new Error("useConfig must be used within a ConfigProvider");
  }
  return context;
};

export const ConfigProvider = ({ children }: { children: React.ReactNode }) => {
  const { data: config, error } = useApiConfig();
  const { setTheme } = useAppContext();

  useEffect(() => {
    if (config?.FRONTEND_THEME) {
      setTheme(config.FRONTEND_THEME);
    }
  }, [config?.FRONTEND_THEME, setTheme]);

  if (config) {
    return <ConfigContext.Provider value={{ config }}>{children}</ConfigContext.Provider>;
  }

  // The application cannot start without its configuration.
  if (error) {
    return <AppErrorScreen error={error} />;
  }

  return (
    <div className="global-loader">
      <Spinner size="xl" />
    </div>
  );
};
