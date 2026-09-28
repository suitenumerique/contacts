import { MainLayout } from "@gouvfr-lasuite/ui-components";
import { Auth } from "@/features/auth/Auth";
import { HeaderIcon, HeaderRight } from "../header/Header";
import { Toaster } from "@/features/ui/components/toaster/Toaster";
import { LeftPanelMobile } from "@/features/layouts/components/left-panel/LeftPanelMobile";
import { useLeftPanel } from "../../contexts/LeftPanelContext";

/** Header-only layout for the error and no-access pages. */
export const SimpleLayout = ({ children }: { children: React.ReactNode }) => {
  const { isLeftPanelOpen, setIsLeftPanelOpen } = useLeftPanel();
  return (
    <Auth>
      <MainLayout
        hideLeftPanelOnDesktop
        isLeftPanelOpen={isLeftPanelOpen}
        setIsLeftPanelOpen={setIsLeftPanelOpen}
        leftPanelContent={<LeftPanelMobile />}
        icon={<HeaderIcon />}
        rightHeaderContent={<HeaderRight />}
      >
        {children}
      </MainLayout>
      <Toaster />
    </Auth>
  );
};
