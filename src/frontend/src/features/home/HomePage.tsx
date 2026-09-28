import { useTranslation } from "react-i18next";
import { Button, Footer, Hero, HomeGutter } from "@gouvfr-lasuite/ui-components";
import { login } from "@/features/auth/Auth";
import { useConfig } from "@/features/config/ConfigProvider";
import { ContactsLogo } from "@/features/ui/components/logo";
import { useThemeCustomization } from "@/hooks/useThemeCustomization";

import banner from "@/assets/home/banner.svg";

export const HomePage = () => {
  const { t } = useTranslation();
  const { config } = useConfig();
  const footerCustomization = useThemeCustomization("footer");

  return (
    <>
      <HomeGutter>
        <Hero
          logo={<ContactsLogo variant="icon" />}
          banner={banner}
          title={t("home.title")}
          subtitle={t("home.subtitle")}
          mainButton={
            <div className="c__hero__buttons">
              <div>
                <Button onClick={() => login()} fullWidth>
                  {t("home.main_button")}
                </Button>
              </div>
              {config.FRONTEND_MORE_LINK && (
                <div>
                  <Button
                    variant="bordered"
                    fullWidth
                    href={config.FRONTEND_MORE_LINK}
                    target="_blank"
                  >
                    {t("home.more")}
                  </Button>
                </div>
              )}
            </div>
          }
        />
      </HomeGutter>
      <Footer {...footerCustomization} />
    </>
  );
};
