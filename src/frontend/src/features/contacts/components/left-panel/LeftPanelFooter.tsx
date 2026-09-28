import { useTranslation } from "react-i18next";
import { Button, HelpMenu, useResponsive } from "@gouvfr-lasuite/ui-components";
import { GearRounded } from "@gouvfr-lasuite/ui-components/icons";
import { useConfig } from "@/features/config/ConfigProvider";
import { useFeedback } from "@/features/feedback/Feedback";
import { Gaufre } from "@/features/ui/components/gaufre/Gaufre";
import { UserProfile } from "@/features/ui/components/user/UserProfile";
import { useContactsModals } from "../../modals";

export const LeftPanelFooter = () => {
  const { t } = useTranslation();
  const { config } = useConfig();
  const { isTablet } = useResponsive();
  const { openModal } = useContactsModals();
  const feedback = useFeedback();

  return (
    <div className="sidebar-footer">
      <div className="sidebar-footer__actions">
        <HelpMenu
          documentationUrl={config.FRONTEND_MORE_LINK || undefined}
          onContactUs={feedback.isAvailable ? feedback.open : undefined}
        />
        <Button
          size="small"
          color="neutral"
          variant="tertiary"
          icon={<GearRounded />}
          aria-label={t("contacts.settings.title")}
          onClick={() => openModal({ kind: "settings" })}
          data-testid="settings-button"
        />
      </div>
      {isTablet && (
        <div className="sidebar-footer__suite">
          <Gaufre />
          <UserProfile />
        </div>
      )}
      {feedback.modal}
    </div>
  );
};
