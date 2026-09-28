/**
 * Confirmation before copying one of the user's contacts into the
 * organization's address book (administrators).
 */

import { useTranslation } from "react-i18next";
import { Alert, Button, Modal, ModalSize, VariantType } from "@gouvfr-lasuite/ui-components";
import { errorToString } from "@/features/api/APIError";
import { useShareContact } from "../../hooks/useContactsQueries";
import type { Contact } from "../../types";

interface ShareContactModalProps {
  isOpen: boolean;
  contact: Pick<Contact, "id" | "display_name">;
  onClose: () => void;
  onShared?: (shared: Contact) => void;
}

export const ShareContactModal = ({
  isOpen,
  contact,
  onClose,
  onShared,
}: ShareContactModalProps) => {
  const { t } = useTranslation();
  const shareContact = useShareContact();

  const handleShare = async () => {
    const response = await shareContact.mutateAsync({ id: contact.id });
    onClose();
    onShared?.(response.data);
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      size={ModalSize.SMALL}
      title={t("contacts.shareModal.title")}
      rightActions={
        <>
          <Button color="neutral" variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => void handleShare()}
            disabled={shareContact.isPending}
            data-testid="confirm-share-contact"
          >
            {t("contacts.shareModal.confirm")}
          </Button>
        </>
      }
    >
      {shareContact.isError && (
        <Alert type={VariantType.ERROR}>{errorToString(shareContact.error as Error)}</Alert>
      )}
      <p>{t("contacts.shareModal.message", { name: contact.display_name })}</p>
    </Modal>
  );
};
