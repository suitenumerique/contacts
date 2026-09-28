/**
 * Create or rename a group. Members are managed from the group pane.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Checkbox,
  Input,
  Modal,
  ModalSize,
  TextArea,
  VariantType,
} from "@gouvfr-lasuite/ui-components";
import { errorToString } from "@/features/api/APIError";
import { useAuth } from "@/features/auth/Auth";
import { useCreateGroup, useUpdateGroup } from "../../hooks/useContactsQueries";
import type { ContactGroup } from "../../types";

interface GroupFormModalProps {
  isOpen: boolean;
  group?: ContactGroup;
  onClose: () => void;
  onSaved?: (group: ContactGroup) => void;
}

export const GroupFormModal = ({ isOpen, group, onClose, onSaved }: GroupFormModalProps) => {
  const { t } = useTranslation();
  const createGroup = useCreateGroup();
  const updateGroup = useUpdateGroup();
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");
  const [isOrgGroup, setIsOrgGroup] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { user } = useAuth();
  const isPending = createGroup.isPending || updateGroup.isPending;

  const handleSave = async () => {
    setError(null);
    const data = { name: name.trim(), description: description.trim() };
    try {
      const saved = group
        ? await updateGroup.mutateAsync({ id: group.id, data })
        : await createGroup.mutateAsync({ data: { ...data, is_org_group: isOrgGroup } });
      onClose();
      onSaved?.(saved.data);
    } catch (err) {
      setError(errorToString(err as Error));
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      size={ModalSize.MEDIUM}
      title={group ? t("contacts.groups.edit") : t("contacts.groups.createTitle")}
      rightActions={
        <>
          <Button color="neutral" variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => void handleSave()}
            disabled={!name.trim() || isPending}
            data-testid="save-group-button"
          >
            {group ? t("common.save") : t("contacts.groups.createConfirm")}
          </Button>
        </>
      }
    >
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) void handleSave();
        }}
      >
        {error && <Alert type={VariantType.ERROR}>{error}</Alert>}
        <Input
          label={t("contacts.groups.name")}
          value={name}
          required
          autoFocus
          fullWidth
          data-testid="group-name-input"
          onChange={(event) => setName(event.target.value)}
        />
        <TextArea
          label={t("contacts.groups.descriptionLabel")}
          value={description}
          rows={3}
          fullWidth
          data-testid="group-description-input"
          onChange={(event) => setDescription(event.target.value)}
        />
        {!group && user?.can_admin && (
          <Checkbox
            label={t("contacts.groups.shareWithOrg")}
            text={t("contacts.groups.shareWithOrgHint")}
            checked={isOrgGroup}
            data-testid="group-org-checkbox"
            onChange={(event) => setIsOrgGroup(event.target.checked)}
          />
        )}
      </form>
    </Modal>
  );
};
