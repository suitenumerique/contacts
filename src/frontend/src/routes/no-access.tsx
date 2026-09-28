import { createFileRoute } from "@tanstack/react-router";
import { Button } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";
import { logout } from "@/features/auth/Auth";
import { SimpleLayout } from "@/features/layouts/components/simple/SimpleLayout";
import { GenericDisclaimer } from "@/features/ui/components/generic-disclaimer/GenericDisclaimer";

const NoAccessPage = () => {
  const { t } = useTranslation();
  return (
    <GenericDisclaimer message={t("no_access.title")} imageSrc="/403-background.png">
      <p>{t("no_access.description")}</p>
      <Button onClick={() => logout()}>{t("no_access.button")}</Button>
    </GenericDisclaimer>
  );
};

export const Route = createFileRoute("/no-access")({
  component: () => (
    <SimpleLayout>
      <NoAccessPage />
    </SimpleLayout>
  ),
});
