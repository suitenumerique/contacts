/**
 * User settings, on Cunningham's "tab" modal: fixed height, one tab per
 * category.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal, ModalSize, useResponsive, type ModalTab } from "@gouvfr-lasuite/ui-components";
import { useConfig } from "@/features/config/ConfigProvider";
import { useRestoreFocusOnNestedModalClose } from "@/features/ui/hooks/useRestoreFocusOnNestedModalClose";
import { CarddavSettings } from "./CarddavSettings";
import { LabelsSettings } from "./LabelsSettings";

type SettingsTab = "labels" | "carddav";

// Cunningham's tab modal switches to a sidebar -> content drill-down below
// this width (its own `@media (max-width: 576px)`).
const COMPACT_TAB_MODAL_MEDIA_QUERY = "(max-width: 576px)";

export const SettingsModal = ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => {
  const { t } = useTranslation();
  const { isMobile } = useResponsive();
  const { config } = useConfig();

  // Label and device forms open stacked modals on top of this one.
  useRestoreFocusOnNestedModalClose(isOpen);

  // Compact layout: open on the bare sidebar ("" keeps Cunningham from
  // selecting the first tab).
  const [activeTab, setActiveTab] = useState<SettingsTab | "">(() =>
    window.matchMedia(COMPACT_TAB_MODAL_MEDIA_QUERY).matches ? "" : "labels",
  );

  const tabs: ModalTab[] = [
    {
      id: "labels",
      label: t("contacts.settings.labels.tab"),
      title: t("contacts.settings.labels.title"),
      subtitle: <p className="mb-base mr-base">{t("contacts.settings.labels.subtitle")}</p>,
      content: <LabelsSettings />,
    },
  ];
  if (config.FEATURE_CARDDAV) {
    tabs.push({
      id: "carddav",
      label: t("contacts.settings.carddav.tab"),
      title: t("contacts.settings.carddav.title"),
      subtitle: <p className="mb-base mr-base">{t("contacts.settings.carddav.subtitle")}</p>,
      content: <CarddavSettings />,
    });
  }

  return (
    <Modal
      constraints={{ preferredHeight: "80vh" }}
      isOpen={isOpen}
      aria-label={t("contacts.settings.title")}
      onClose={onClose}
      size={isMobile ? ModalSize.FULL : ModalSize.LARGE}
      variant="tab"
      sidebarTitle={<div className="settings__title">{t("contacts.settings.title")}</div>}
      tabs={tabs}
      activeTab={activeTab}
      onTabChange={(tabId) => setActiveTab(tabId as SettingsTab)}
    />
  );
};
