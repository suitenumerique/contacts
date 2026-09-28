/**
 * CardDAV tab: connection details, then one password per phone or desktop
 * client, shown once at creation and revocable at any time.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  DataGrid,
  IconSize,
  Spinner,
  VariantType,
  useModals,
  useResponsive,
  type Column,
} from "@gouvfr-lasuite/ui-components";
import { Plus, Trash } from "@gouvfr-lasuite/ui-components/icons";
import { getOrigin } from "@/features/api/utils";
import { useAuth } from "@/features/auth/Auth";
import { CopyableInput } from "@/features/ui/components/copyable-input/CopyableInput";
import { addErrorToast, addSuccessToast } from "@/features/ui/components/toaster/Toaster";
import { useCarddavChannels, useDeleteChannel } from "../../hooks/useContactsQueries";
import type { Channel } from "../../types";
import { CarddavDeviceModal } from "./CarddavDeviceModal";
import { ResourceSectionHeader } from "./ResourceSectionHeader";

// DataGrid rows need an index signature, which generated interfaces lack.
type ChannelRow = Pick<Channel, keyof Channel>;

export const CarddavSettings = () => {
  const { t, i18n } = useTranslation();
  const { isMobile } = useResponsive();
  const modals = useModals();
  const { user } = useAuth();
  const { channels, isLoading, error } = useCarddavChannels();
  const deleteChannel = useDeleteChannel();
  const [isCreating, setIsCreating] = useState(false);

  const serverUrl = new URL("/carddav/", getOrigin()).href;
  const formatDate = (value: string | null) =>
    value
      ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" }).format(new Date(value))
      : t("contacts.settings.carddav.never");

  const handleRevoke = async (channel: Channel) => {
    const decision = await modals.deleteConfirmationModal({
      title: t("contacts.settings.carddav.revokeTitle", { name: channel.name }),
      children: t("contacts.settings.carddav.revokeConfirm"),
    });
    if (decision !== "delete") return;
    try {
      await deleteChannel.mutateAsync({ id: channel.id });
      addSuccessToast(t("contacts.settings.carddav.revoked"));
    } catch {
      addErrorToast(t("contacts.settings.carddav.revokeFailed"));
    }
  };

  const columns: Column<ChannelRow>[] = [
    {
      id: "name",
      headerName: t("contacts.settings.carddav.device"),
      renderCell: ({ row }) => row.name,
    },
    {
      id: "access",
      headerName: t("contacts.settings.carddav.access"),
      size: 200,
      renderCell: ({ row }) => {
        const access = t(
          row.scopes.includes("carddav:write")
            ? "contacts.settings.carddav.readWrite"
            : "contacts.settings.carddav.readOnly",
        );
        return row.groups ? `${access}, ${t("contacts.settings.carddav.withGroups")}` : access;
      },
    },
    {
      id: "last_used_at",
      headerName: t("contacts.settings.carddav.lastUsed"),
      size: 150,
      renderCell: ({ row }) => formatDate(row.last_used_at),
    },
    {
      id: "actions",
      headerName: t("contacts.settings.actions"),
      size: 110,
      renderCell: ({ row }) => (
        <div className="settings__row-actions">
          <Button
            color="error"
            variant="tertiary"
            size="nano"
            icon={<Trash size={IconSize.SMALL} />}
            disabled={deleteChannel.isPending}
            onClick={() => void handleRevoke(row)}
            data-testid={`carddav-revoke-${row.id}`}
          >
            {t("contacts.settings.carddav.revoke")}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="settings__tab">
      <section className="settings__section">
        <h3 className="settings__section-title">{t("contacts.settings.carddav.connection")}</h3>
        <div className="settings__fields">
          <label className="settings__field-label" htmlFor="carddav-server">
            {t("contacts.settings.carddav.server")}
          </label>
          <CopyableInput id="carddav-server" value={serverUrl} />
          <label className="settings__field-label" htmlFor="carddav-username">
            {t("contacts.settings.carddav.username")}
          </label>
          <CopyableInput id="carddav-username" value={user?.email ?? ""} />
        </div>
      </section>

      <section className="settings__section">
        <ResourceSectionHeader
          label={
            isLoading
              ? undefined
              : channels.length === 0
                ? t("contacts.settings.carddav.empty")
                : t("contacts.settings.carddav.count", { count: channels.length })
          }
          action={
            <Button
              size={isMobile ? "small" : "nano"}
              icon={<Plus />}
              aria-label={isMobile ? t("contacts.settings.carddav.newDevice") : undefined}
              onClick={() => setIsCreating(true)}
              data-testid="carddav-new-device"
            >
              {!isMobile && t("contacts.settings.carddav.newDevice")}
            </Button>
          }
        />
        <div className="admin-data-grid">
          {isLoading ? (
            <Spinner />
          ) : error ? (
            <Alert type={VariantType.ERROR}>{t("contacts.settings.loadError")}</Alert>
          ) : (
            <DataGrid
              columns={columns}
              rows={channels}
              enableSorting={false}
              emptyPlaceholderLabel={t("contacts.settings.carddav.empty")}
            />
          )}
        </div>
      </section>
      {isCreating && <CarddavDeviceModal onClose={() => setIsCreating(false)} />}
    </div>
  );
};
