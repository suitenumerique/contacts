/**
 * One place that renders the feature's modals, so any pane can open them.
 */

import { createContext, PropsWithChildren, useContext, useMemo, useState } from "react";
import { useNavigate, useRouter } from "@tanstack/react-router";
import { ContactFormModal } from "./components/contact-form/ContactFormModal";
import { DeleteContactModal } from "./components/contact-detail/DeleteContactModal";
import { ShareContactModal } from "./components/contact-detail/ShareContactModal";
import { DeleteGroupModal } from "./components/groups/DeleteGroupModal";
import { GroupFormModal } from "./components/groups/GroupFormModal";
import { ImportContactsModal } from "./components/import/ImportContactsModal";
import { SettingsModal } from "./components/settings/SettingsModal";
import type { Contact, ContactGroup, ContactListItem } from "./types";

type ModalState =
  /** `forkSourceId`: edit a local copy of that read-only contact. */
  | { kind: "contact-form"; contact: Contact | null; forkSourceId?: string }
  | { kind: "delete-contact"; contact: Contact | ContactListItem; permanent: boolean }
  | { kind: "share-contact"; contact: Contact }
  | { kind: "group-form"; group?: ContactGroup }
  | { kind: "delete-group"; group: ContactGroup }
  | { kind: "import" }
  | { kind: "settings" };

interface ModalsContextValue {
  openModal: (modal: ModalState) => void;
}

const ModalsContext = createContext<ModalsContextValue | null>(null);

export const ContactsModalsProvider = ({ children }: PropsWithChildren) => {
  const navigate = useNavigate();
  const router = useRouter();
  const [modal, setModal] = useState<ModalState | null>(null);
  const isOpenInPane = (id: string) => router.state.location.pathname.endsWith(`/${id}`);
  const close = () => setModal(null);
  const value = useMemo(() => ({ openModal: setModal }), []);

  return (
    <ModalsContext.Provider value={value}>
      {children}
      {modal?.kind === "contact-form" && (
        <ContactFormModal
          isOpen
          contact={modal.contact}
          forkSourceId={modal.forkSourceId}
          onClose={close}
          onSaved={(saved) =>
            void navigate({
              to: "/contacts/{-$contactId}",
              params: { contactId: saved.id },
              search: (prev) => prev,
            })
          }
        />
      )}
      {modal?.kind === "delete-contact" && (
        <DeleteContactModal
          isOpen
          contact={modal.contact}
          permanent={modal.permanent}
          onClose={close}
          onDeleted={() => {
            if (isOpenInPane(modal.contact.id)) {
              void navigate({
                to: modal.permanent ? "/trash/{-$contactId}" : "/contacts/{-$contactId}",
                params: { contactId: undefined },
                search: (prev) => prev,
              });
            }
          }}
        />
      )}
      {modal?.kind === "share-contact" && (
        <ShareContactModal
          isOpen
          contact={modal.contact}
          onClose={close}
          onShared={(shared) =>
            void navigate({
              to: "/contacts/{-$contactId}",
              params: { contactId: shared.id },
              search: (prev) => prev,
            })
          }
        />
      )}
      {modal?.kind === "group-form" && (
        <GroupFormModal
          isOpen
          group={modal.group}
          onClose={close}
          onSaved={(saved) =>
            void navigate({ to: "/groups/{-$groupId}", params: { groupId: saved.id } })
          }
        />
      )}
      {modal?.kind === "delete-group" && (
        <DeleteGroupModal
          isOpen
          group={modal.group}
          onClose={close}
          onDeleted={() => {
            if (isOpenInPane(modal.group.id)) {
              void navigate({ to: "/groups/{-$groupId}", params: { groupId: undefined } });
            }
          }}
        />
      )}
      {modal?.kind === "import" && <ImportContactsModal isOpen onClose={close} />}
      {modal?.kind === "settings" && <SettingsModal isOpen onClose={close} />}
    </ModalsContext.Provider>
  );
};

export const useContactsModals = () => {
  const context = useContext(ModalsContext);
  if (!context) {
    throw new Error("useContactsModals must be used within a ContactsModalsProvider");
  }
  return context;
};
