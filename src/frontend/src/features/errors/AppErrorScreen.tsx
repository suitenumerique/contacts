import { useEffect } from "react";
import { Button } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";

/**
 * Page for an error that stops the application or a route.
 *
 * It uses no layout and no context of the application. The error can
 * come from that code, and this page must still render.
 */
export const AppErrorScreen = ({ error }: { error: unknown }) => {
  const { t } = useTranslation();

  useEffect(() => {
    console.error("Unhandled application error", error);
  }, [error]);

  return (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "1rem",
        // Fill the window, but not more than a parent with a fixed height.
        // The pane of the app layout is such a parent.
        height: "100vh",
        maxHeight: "100%",
        padding: "2rem",
        textAlign: "center",
      }}
    >
      <h1>{t("error_screen.title")}</h1>
      <p>{t("error_screen.description")}</p>
      <Button onClick={() => window.location.reload()}>{t("error_screen.reload")}</Button>
    </div>
  );
};
