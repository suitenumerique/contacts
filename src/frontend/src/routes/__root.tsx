import { createRootRoute, Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import {
  MutationCache,
  Query,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";
import { AppContextProvider, useAppContext } from "@/features/app/AppContext";
import { ConfigProvider } from "@/features/config/ConfigProvider";
import { LeftPanelProvider } from "@/features/layouts/contexts/LeftPanelContext";
import { APIError, errorToString } from "@/features/api/APIError";
import { capitalizeRegion } from "@/features/i18n/utils";
import { addToast, ToasterItem } from "@/features/ui/components/toaster/Toaster";

const onError = (error: Error, query: unknown) => {
  if ((query as Query).meta?.noGlobalError) {
    return;
  }
  // 401/403 lead to a redirect: a toast right before it would look broken.
  if (error instanceof APIError) {
    if (error.code === 401) {
      return;
    }
    if (error.code === 403 && !(query as Query).meta?.showErrorOn403) {
      return;
    }
  }
  addToast(
    <ToasterItem type="error">
      <span>{errorToString(error)}</span>
    </ToasterItem>,
  );
};

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => onError(error, mutation),
  }),
  queryCache: new QueryCache({
    onError: (error, query) => onError(error, query),
  }),
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
});

const RootShell = () => {
  const { t, i18n } = useTranslation();
  const { theme } = useAppContext();

  useEffect(() => {
    document.title = t("app_title");
  }, [t]);

  return (
    <QueryClientProvider client={queryClient}>
      <CunninghamProvider currentLocale={capitalizeRegion(i18n.language)} theme={theme}>
        <ConfigProvider>
          <LeftPanelProvider>
            <Outlet />
          </LeftPanelProvider>
        </ConfigProvider>
      </CunninghamProvider>
    </QueryClientProvider>
  );
};

const Root = () => (
  <AppContextProvider>
    <RootShell />
  </AppContextProvider>
);

export const Route = createRootRoute({
  component: Root,
});
