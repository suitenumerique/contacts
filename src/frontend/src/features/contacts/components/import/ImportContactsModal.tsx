/**
 * Upload a vCard (.vcf) or CSV file; the backend imports it asynchronously
 * and a summary shows when the task finishes.
 */

import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Checkbox,
  Modal,
  ModalSize,
  VariantType,
} from "@gouvfr-lasuite/ui-components";
import { Spinner } from "@gouvfr-lasuite/ui-components";
import { Upload } from "@gouvfr-lasuite/ui-components/icons";
import { errorToString } from "@/features/api/APIError";
import { useAuth } from "@/features/auth/Auth";
import { useImportContacts } from "../../hooks/useContactsQueries";
import type { ImportTaskStatus } from "../../types";

interface ImportContactsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const ImportContactsModal = ({ isOpen, onClose }: ImportContactsModalProps) => {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportTaskStatus | null>(null);
  const [intoOrganization, setIntoOrganization] = useState(false);
  const { user } = useAuth();
  const importContacts = useImportContacts();

  const handleImport = async () => {
    if (!selectedFile) return;
    const status = await importContacts.mutateAsync({
      file: selectedFile,
      scope: intoOrganization ? "organization" : "personal",
    });
    setResult(status);
  };

  const handleClose = () => {
    setSelectedFile(null);
    setResult(null);
    importContacts.reset();
    onClose();
  };

  const summary = result?.result;

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      size={ModalSize.MEDIUM}
      title={t("contacts.import.title")}
      rightActions={
        summary ? (
          <Button color="brand" onClick={handleClose}>
            {t("contacts.import.close")}
          </Button>
        ) : (
          <>
            <Button color="neutral" variant="secondary" onClick={handleClose}>
              {t("common.cancel")}
            </Button>
            <Button
              color="brand"
              onClick={() => void handleImport()}
              disabled={!selectedFile || importContacts.isPending}
              data-testid="import-confirm"
            >
              {importContacts.isPending ? <Spinner size="sm" /> : t("contacts.import.import")}
            </Button>
          </>
        )
      }
    >
      <div className="import-vcf-modal">
        <p>{t("contacts.import.description")}</p>
        <p className="contact-form__hint">{t("contacts.import.csvHint")}</p>

        {!summary && (
          <div className="import-vcf-modal__file-section">
            <input
              ref={fileInputRef}
              type="file"
              accept=".vcf,.vcard,.csv,text/vcard,text/csv"
              data-testid="import-file-input"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
              style={{ display: "none" }}
            />
            <Button
              color="neutral"
              variant="secondary"
              onClick={() => fileInputRef.current?.click()}
              icon={<Upload />}
            >
              {t("contacts.import.selectFile")}
            </Button>
            {selectedFile && (
              <span className="import-vcf-modal__filename">{selectedFile.name}</span>
            )}
          </div>
        )}

        {!summary && user?.can_admin && (
          <Checkbox
            label={t("contacts.import.intoOrganization")}
            text={t("contacts.import.intoOrganizationHint")}
            checked={intoOrganization}
            data-testid="import-into-organization"
            onChange={(event) => setIntoOrganization(event.target.checked)}
          />
        )}

        {importContacts.isError && (
          <Alert type={VariantType.ERROR}>{errorToString(importContacts.error as Error)}</Alert>
        )}

        {result?.status === "FAILURE" && (
          <Alert type={VariantType.ERROR}>{t("contacts.import.error")}</Alert>
        )}

        {summary && (
          <Alert
            type={summary.errors > 0 ? VariantType.WARNING : VariantType.SUCCESS}
            data-testid="import-result"
          >
            {t("contacts.import.result", {
              imported: summary.imported,
              skipped: summary.skipped,
              errors: summary.errors,
            })}
            {summary.groups > 0 &&
              ` ${t("contacts.import.resultGroups", { count: summary.groups })}`}
          </Alert>
        )}

        {summary && summary.error_details && summary.error_details.length > 0 && (
          <div className="import-vcf-modal__errors" data-testid="import-error-details">
            <p className="contact-form__hint">{t("contacts.import.errorsTitle")}</p>
            <ul>
              {summary.error_details.map((detail, index) => (
                <li key={`${detail.entry}-${index}`}>
                  <span className="import-vcf-modal__errors__where">
                    {detail.entry === null
                      ? t("contacts.import.wholeFile")
                      : t("contacts.import.entry", {
                          entry: detail.entry,
                          name: detail.name || t("contacts.import.unnamed"),
                        })}
                  </span>
                  <span>
                    {t(`contacts.import.reasons.${detail.reason}`, {
                      value: detail.value ?? "",
                    })}
                  </span>
                </li>
              ))}
            </ul>
            {summary.errors > summary.error_details.length && (
              <p className="contact-form__hint">
                {t("contacts.import.moreErrors", {
                  count: summary.errors - summary.error_details.length,
                })}
              </p>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
};
