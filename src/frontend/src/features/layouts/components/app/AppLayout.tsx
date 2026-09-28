import { PropsWithChildren } from "react";
import { MainLayout } from "@gouvfr-lasuite/ui-components";
import { LeftPanel } from "@/features/contacts/components/left-panel/LeftPanel";
import { LeftPanelFooter } from "@/features/contacts/components/left-panel/LeftPanelFooter";
import { HeaderSearch } from "@/features/contacts/components/search/HeaderSearch";
import { ContactsModalsProvider } from "@/features/contacts/modals";
import { HeaderIcon, HeaderRight } from "@/features/layouts/components/header/Header";
import { useLeftPanel } from "@/features/layouts/contexts/LeftPanelContext";
import { Toaster } from "@/features/ui/components/toaster/Toaster";

/** Shell of the signed-in app: header, sidebar, and the routed page. */
export const AppLayout = ({ children }: PropsWithChildren) => {
  const { isLeftPanelOpen, setIsLeftPanelOpen } = useLeftPanel();

  return (
    <ContactsModalsProvider>
      <div className="app-layout">
        <MainLayout
          icon={
            <>
              <HeaderIcon />
              <HeaderSearch />
            </>
          }
          rightHeaderContent={<HeaderRight />}
          leftPanelContent={<LeftPanel />}
          leftPanelFooter={<LeftPanelFooter />}
          isLeftPanelOpen={isLeftPanelOpen}
          setIsLeftPanelOpen={setIsLeftPanelOpen}
        >
          {children}
        </MainLayout>
        <Toaster />
      </div>
    </ContactsModalsProvider>
  );
};
