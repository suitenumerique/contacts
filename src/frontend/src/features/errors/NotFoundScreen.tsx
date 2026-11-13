import { Button } from "@gouvfr-lasuite/ui-components";
import { House } from "@gouvfr-lasuite/ui-components/icons";
import { useTranslation } from "react-i18next";
import { SimpleLayout } from "@/features/layouts/components/simple/SimpleLayout";
import { GenericDisclaimer } from "@/features/ui/components/generic-disclaimer/GenericDisclaimer";

/** Page for a URL that matches no route. */
export const NotFoundScreen = () => {
  const { t } = useTranslation();
  return (
    <SimpleLayout>
      <GenericDisclaimer message={t("404.title")} imageSrc="/403-background.png">
        <Button href="/" icon={<House />}>
          {t("404.button")}
        </Button>
      </GenericDisclaimer>
    </SimpleLayout>
  );
};
