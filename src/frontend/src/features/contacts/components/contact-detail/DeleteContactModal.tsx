/**
 * Confirmation before deleting a contact: to the trash, forever (from the
 * trash), or, for a local copy, back to the source version.
 */

import { useTranslation } from "react-i18next";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/ui-components";
import { useDeleteContact } from "../../hooks/useContactsQueries";
import type { Contact } from "../../types";

interface DeleteContactModalProps {
  isOpen: boolean;
  contact: Pick<Contact, "id" | "display_name" | "overlay_of" | "is_org_contact">;
  onClose: () => void;
  onDeleted?: () => void;
  permanent?: boolean;
}

/**
 * i18n key prefix: deleting an overlay only drops the local edits, and
 * deleting a shared contact removes it for the whole organization.
 */
export const deleteModalPrefix = (
  contact: Pick<Contact, "overlay_of" | "is_org_contact">,
  permanent?: boolean,
) => {
  if (contact.is_org_contact) {
    return permanent ? "contacts.deleteSharedForeverModal" : "contacts.deleteSharedModal";
  }
  if (permanent) return "contacts.deleteForeverModal";
  return contact.overlay_of ? "contacts.revertModal" : "contacts.deleteModal";
};

export const DeleteContactModal = ({
  isOpen,
  contact,
  onClose,
  onDeleted,
  permanent,
}: DeleteContactModalProps) => {
  const { t } = useTranslation();
  const deleteContact = useDeleteContact();
  const prefix = deleteModalPrefix(contact, permanent);

  const handleDelete = async () => {
    await deleteContact.mutateAsync({
      id: contact.id,
      params: permanent ? { permanent: true } : undefined,
    });
    onClose();
    onDeleted?.();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      size={ModalSize.SMALL}
      title={t(`${prefix}.title`)}
      rightActions={
        <>
          <Button color="neutral" variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            color="error"
            onClick={() => void handleDelete()}
            disabled={deleteContact.isPending}
            data-testid="confirm-delete-contact"
          >
            {t(`${prefix}.confirm`)}
          </Button>
        </>
      }
    >
      <p>{t(`${prefix}.message`, { name: contact.display_name })}</p>
    </Modal>
  );
};
