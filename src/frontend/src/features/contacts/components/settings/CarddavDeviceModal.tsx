/**
 * Create a CardDAV device password, then show it once.
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
  VariantType,
} from "@gouvfr-lasuite/ui-components";
import { errorToString } from "@/features/api/APIError";
import { CopyableInput } from "@/features/ui/components/copyable-input/CopyableInput";
import { useCreateChannel } from "../../hooks/useContactsQueries";
import type { ChannelWithKey } from "../../types";
import { bidiIsolate } from "../../utils";

export const CarddavDeviceModal = ({ onClose }: { onClose: () => void }) => {
  const { t } = useTranslation();
  const createChannel = useCreateChannel();
  const [name, setName] = useState("");
  const [canWrite, setCanWrite] = useState(true);
  const [withGroups, setWithGroups] = useState(false);
  const [created, setCreated] = useState<ChannelWithKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canCreate = !!name.trim() && !createChannel.isPending;

  const create = async () => {
    setError(null);
    try {
      const response = await createChannel.mutateAsync({
        data: {
          name: name.trim(),
          type: "carddav",
          scopes: [canWrite ? "carddav:write" : "carddav:read"],
          groups: withGroups,
        },
      });
      setCreated(response.data);
    } catch (err) {
      setError(errorToString(err as Error));
    }
  };

  return (
    <Modal
      isOpen
      onClose={onClose}
      size={ModalSize.MEDIUM}
      title={
        created
          ? t("contacts.settings.carddav.savePassword")
          : t("contacts.settings.carddav.newDevice")
      }
      rightActions={
        created ? (
          <Button onClick={onClose} data-testid="carddav-done">
            {t("contacts.settings.carddav.done")}
          </Button>
        ) : (
          <>
            <Button color="neutral" variant="secondary" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void create()}
              disabled={!canCreate}
              data-testid="carddav-create"
            >
              {t("contacts.settings.carddav.create")}
            </Button>
          </>
        )
      }
    >
      {created?.carddav_password ? (
        <div className="modal-form">
          <Alert type={VariantType.WARNING}>
            {t("contacts.settings.carddav.createdOnce", { name: bidiIsolate(created.name) })}
          </Alert>
          <label className="settings__field-label" htmlFor="carddav-password">
            {t("contacts.settings.carddav.password")}
          </label>
          <CopyableInput
            id="carddav-password"
            value={created.carddav_password}
            data-testid="carddav-password"
          />
        </div>
      ) : (
        <form
          className="modal-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (canCreate) void create();
          }}
        >
          {error && <Alert type={VariantType.ERROR}>{error}</Alert>}
          <Input
            label={t("contacts.settings.carddav.deviceName")}
            value={name}
            required
            autoFocus
            fullWidth
            data-testid="carddav-name-input"
            onChange={(event) => setName(event.target.value)}
          />
          <Checkbox
            label={t("contacts.settings.carddav.allowWrite")}
            checked={canWrite}
            onChange={(event) => setCanWrite(event.target.checked)}
          />
          <Checkbox
            label={t("contacts.settings.carddav.syncGroups")}
            text={t("contacts.settings.carddav.syncGroupsHint")}
            checked={withGroups}
            onChange={(event) => setWithGroups(event.target.checked)}
          />
        </form>
      )}
    </Modal>
  );
};
