import { createFileRoute } from "@tanstack/react-router";
import { Button } from "@gouvfr-lasuite/ui-components";
import { House } from "@gouvfr-lasuite/ui-components/icons";
import { useTranslation } from "react-i18next";
import { SimpleLayout } from "@/features/layouts/components/simple/SimpleLayout";
import { GenericDisclaimer } from "@/features/ui/components/generic-disclaimer/GenericDisclaimer";

const ForbiddenPage = () => {
  const { t } = useTranslation();
  return (
    <GenericDisclaimer message={t("403.title")} imageSrc="/403-background.png">
      <Button href="/" icon={<House />}>
        {t("403.button")}
      </Button>
    </GenericDisclaimer>
  );
};

const ForbiddenRoute = () => (
  <SimpleLayout>
    <ForbiddenPage />
  </SimpleLayout>
);

export const Route = createFileRoute("/403")({
  component: ForbiddenRoute,
});
