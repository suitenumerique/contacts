import { createFileRoute, Navigate, useRouter } from "@tanstack/react-router";
import { MainLayout } from "@gouvfr-lasuite/ui-components";
import { useEffect } from "react";

import { Auth, useAuth } from "@/features/auth/Auth";
import { SESSION_STORAGE_REDIRECT_AFTER_LOGIN_URL } from "@/features/api/fetchApi";
import { HomePage } from "@/features/home/HomePage";
import { HeaderIcon, HeaderRight } from "@/features/layouts/components/header/Header";
import { LeftPanelMobile } from "@/features/layouts/components/left-panel/LeftPanelMobile";
import { SpinnerPage } from "@/features/ui/components/spinner/SpinnerPage";
import { Toaster } from "@/features/ui/components/toaster/Toaster";

/** Where to go once signed in: the page a 401 interrupted, else the address book. */
const popRedirectAfterLogin = (): string => {
  try {
    const attempted = sessionStorage.getItem(SESSION_STORAGE_REDIRECT_AFTER_LOGIN_URL);
    sessionStorage.removeItem(SESSION_STORAGE_REDIRECT_AFTER_LOGIN_URL);
    if (attempted) {
      const url = new URL(attempted, window.location.origin);
      if (url.origin === window.location.origin && url.pathname !== "/") {
        return url.pathname + url.search + url.hash;
      }
    }
  } catch {
    // Storage unavailable or invalid URL: fall back to the default page.
  }
  return "/contacts";
};

const AnonymousView = () => (
  <div className="contacts__home">
    <MainLayout
      hideLeftPanelOnDesktop
      leftPanelContent={<LeftPanelMobile />}
      icon={<HeaderIcon />}
      rightHeaderContent={<HeaderRight />}
    >
      <HomePage />
    </MainLayout>
    <Toaster />
  </div>
);

const RedirectAfterLogin = () => {
  const router = useRouter();
  useEffect(() => {
    router.history.replace(popRedirectAfterLogin());
  }, [router]);
  return <SpinnerPage />;
};

const IndexContent = () => {
  const { user } = useAuth();

  if (!user) {
    return <AnonymousView />;
  }
  if (user.can_access === false) {
    return <Navigate to="/no-access" replace />;
  }
  return <RedirectAfterLogin />;
};

export const Route = createFileRoute("/")({
  component: () => (
    <Auth>
      <IndexContent />
    </Auth>
  ),
});
