/**
 * Confirmation before deleting a group. Its contacts are kept.
 */

import { useTranslation } from "react-i18next";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/ui-components";
import { useDeleteGroup } from "../../hooks/useContactsQueries";
import type { ContactGroup } from "../../types";

interface DeleteGroupModalProps {
  isOpen: boolean;
  group: ContactGroup;
  onClose: () => void;
  onDeleted?: () => void;
}

export const DeleteGroupModal = ({ isOpen, group, onClose, onDeleted }: DeleteGroupModalProps) => {
  const { t } = useTranslation();
  const deleteGroup = useDeleteGroup();

  const handleDelete = async () => {
    await deleteGroup.mutateAsync({ id: group.id });
    onClose();
    onDeleted?.();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      size={ModalSize.SMALL}
      title={t("contacts.groups.deleteModal.title")}
      rightActions={
        <>
          <Button color="neutral" variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            color="error"
            onClick={() => void handleDelete()}
            disabled={deleteGroup.isPending}
            data-testid="confirm-delete-group"
          >
            {t("contacts.groups.deleteModal.confirm")}
          </Button>
        </>
      }
    >
      <p>{t("contacts.groups.deleteModal.message", { name: group.name })}</p>
    </Modal>
  );
};
