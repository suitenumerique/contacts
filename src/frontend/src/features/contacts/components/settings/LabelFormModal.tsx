/**
 * Create or edit a label, stacked on the settings modal.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { Alert, Button, Input, Modal, ModalSize, VariantType } from "@gouvfr-lasuite/ui-components";
import { errorToString } from "@/features/api/APIError";
import { useCreateLabel, useUpdateLabel } from "../../hooks/useContactsQueries";
import type { ContactLabel } from "../../types";
import { LabelBadge } from "../labels/LabelBadge";

export const LABEL_COLORS = [
  "#0063CB",
  "#B34000",
  "#006A6F",
  "#6E445A",
  "#695240",
  "#447049",
  "#C03A5A",
  "#8B5CC4",
];

export const LabelFormModal = ({
  label,
  onClose,
}: {
  /** Omitted to create a new label. */
  label?: ContactLabel;
  onClose: () => void;
}) => {
  const { t } = useTranslation();
  const createLabel = useCreateLabel();
  const updateLabel = useUpdateLabel();
  const [name, setName] = useState(label?.name ?? "");
  const [color, setColor] = useState(label?.color ?? LABEL_COLORS[0]);
  const [error, setError] = useState<string | null>(null);
  const isPending = createLabel.isPending || updateLabel.isPending;
  const canSave = !!name.trim() && !isPending;

  const save = async () => {
    setError(null);
    const data = { name: name.trim(), color };
    try {
      if (label) {
        await updateLabel.mutateAsync({ id: label.id, data });
      } else {
        await createLabel.mutateAsync({ data });
      }
      onClose();
    } catch (err) {
      setError(errorToString(err as Error));
    }
  };

  return (
    <Modal
      isOpen
      onClose={onClose}
      size={ModalSize.MEDIUM}
      title={label ? t("contacts.labels.editTitle") : t("contacts.labels.create")}
      rightActions={
        <>
          <Button color="neutral" variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button onClick={() => void save()} disabled={!canSave} data-testid="save-label-button">
            {t("common.save")}
          </Button>
        </>
      }
    >
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSave) void save();
        }}
      >
        {error && <Alert type={VariantType.ERROR}>{error}</Alert>}
        <Input
          label={t("contacts.labels.name")}
          value={name}
          required
          autoFocus
          fullWidth
          data-testid="label-name-input"
          onChange={(event) => setName(event.target.value)}
        />
        <div
          className="label-form__colors"
          role="radiogroup"
          aria-label={t("contacts.labels.color")}
        >
          {LABEL_COLORS.map((candidate) => (
            <button
              key={candidate}
              type="button"
              role="radio"
              aria-checked={color === candidate}
              aria-label={candidate}
              className={clsx("label-form__swatch", {
                "label-form__swatch--selected": color === candidate,
              })}
              style={{ backgroundColor: candidate }}
              onClick={() => setColor(candidate)}
            />
          ))}
        </div>
        <div className="label-form__preview">
          <span>{t("contacts.labels.preview")}</span>
          <LabelBadge label={{ name: name.trim() || t("contacts.labels.name"), color }} />
        </div>
      </form>
    </Modal>
  );
};
