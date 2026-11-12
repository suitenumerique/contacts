/**
 * Labels tab: the user's labels in a data grid, created and edited in a
 * stacked modal.
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
import { addErrorToast, addSuccessToast } from "@/features/ui/components/toaster/Toaster";
import { useDeleteLabel, useLabels } from "../../hooks/useContactsQueries";
import type { ContactLabel } from "../../types";
import { bidiIsolate } from "../../utils";
import { LabelBadge } from "../labels/LabelBadge";
import { LabelFormModal } from "./LabelFormModal";
import { ResourceSectionHeader } from "./ResourceSectionHeader";

// DataGrid rows need an index signature, which generated interfaces lack.
type LabelRow = Pick<ContactLabel, keyof ContactLabel>;

export const LabelsSettings = () => {
  const { t } = useTranslation();
  const { isMobile } = useResponsive();
  const modals = useModals();
  const { labels, isLoading, error } = useLabels();
  const deleteLabel = useDeleteLabel();
  // `null` creates a new label; `undefined` keeps the form closed.
  const [editedLabel, setEditedLabel] = useState<ContactLabel | null>();

  const handleDelete = async (label: ContactLabel) => {
    const decision = await modals.deleteConfirmationModal({
      title: t("contacts.labels.deleteTitle", { name: bidiIsolate(label.name) }),
      children: t("contacts.labels.confirmDelete"),
    });
    if (decision !== "delete") return;
    try {
      await deleteLabel.mutateAsync({ id: label.id });
      addSuccessToast(t("contacts.labels.deleted"));
    } catch {
      addErrorToast(t("contacts.labels.deleteFailed"));
    }
  };

  const columns: Column<LabelRow>[] = [
    {
      id: "name",
      headerName: t("contacts.labels.name"),
      renderCell: ({ row }) => <LabelBadge label={row} />,
    },
    {
      id: "contacts",
      headerName: t("contacts.nav.contacts"),
      size: 130,
      renderCell: ({ row }) => t("contacts.list.count", { count: row.contacts_count }),
    },
    {
      id: "actions",
      headerName: t("contacts.settings.actions"),
      size: 130,
      renderCell: ({ row }) => (
        <div className="settings__row-actions">
          <Button variant="tertiary" size="nano" onClick={() => setEditedLabel(row)}>
            {t("contacts.settings.modify")}
          </Button>
          <Button
            color="error"
            variant="tertiary"
            size="nano"
            icon={<Trash size={IconSize.SMALL} />}
            aria-label={t("contacts.labels.deleteNamed", { name: row.name })}
            disabled={deleteLabel.isPending}
            onClick={() => void handleDelete(row)}
            data-testid={`delete-label-${row.id}`}
          />
        </div>
      ),
    },
  ];

  return (
    <div className="settings__tab">
      <section className="settings__section">
        <ResourceSectionHeader
          label={
            isLoading
              ? undefined
              : labels.length === 0
                ? t("contacts.labels.empty")
                : t("contacts.labels.count", { count: labels.length })
          }
          action={
            <Button
              size={isMobile ? "small" : "nano"}
              icon={<Plus />}
              aria-label={isMobile ? t("contacts.labels.create") : undefined}
              onClick={() => setEditedLabel(null)}
              data-testid="new-label-button"
            >
              {!isMobile && t("contacts.labels.create")}
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
              rows={labels}
              enableSorting={false}
              emptyPlaceholderLabel={t("contacts.labels.empty")}
            />
          )}
        </div>
      </section>
      {editedLabel !== undefined && (
        <LabelFormModal
          label={editedLabel ?? undefined}
          onClose={() => setEditedLabel(undefined)}
        />
      )}
    </div>
  );
};
