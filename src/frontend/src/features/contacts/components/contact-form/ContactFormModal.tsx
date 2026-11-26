/**
 * Create or edit a contact: identity (a name required, in whatever parts
 * are known), coordinates, organization, labels, notes.
 */

import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Checkbox,
  Input,
  Modal,
  ModalSize,
  Select,
  TextArea,
  VariantType,
} from "@gouvfr-lasuite/ui-components";
import { useAuth } from "@/features/auth/Auth";
import { ImageAdd, Plus, XMark } from "@gouvfr-lasuite/ui-components/icons";
import { errorToString } from "@/features/api/APIError";
import { useConfig } from "@/features/config/ConfigProvider";
import {
  useCreateContact,
  useForkContact,
  useLabels,
  useUpdateContact,
} from "../../hooks/useContactsQueries";
import { composedNameOf } from "../../utils";
import { ContactAvatar } from "../avatar/ContactAvatar";
import type { Contact, ContactPayload, TypedValue } from "../../types";

const PHOTO_SIZE = 192;

/** Resize an image file to a small square JPEG data URI (stored in DB). */
const resizePhoto = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      const canvas = document.createElement("canvas");
      canvas.width = PHOTO_SIZE;
      canvas.height = PHOTO_SIZE;
      const context = canvas.getContext("2d");
      if (!context) {
        reject(new Error("canvas unavailable"));
        return;
      }
      const side = Math.min(image.width, image.height);
      context.drawImage(
        image,
        (image.width - side) / 2,
        (image.height - side) / 2,
        side,
        side,
        0,
        0,
        PHOTO_SIZE,
        PHOTO_SIZE,
      );
      resolve(canvas.toDataURL("image/jpeg", 0.82));
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("invalid image"));
    };
    image.src = url;
  });

const cleanRows = (rows: TypedValue[]) =>
  rows.map((row) => ({ ...row, value: row.value.trim() })).filter((row) => row.value);

const TYPE_CHOICES = ["work", "home", "cell", "other"];

const TypedValueRows = ({
  label,
  rows,
  setRows,
  inputType,
  testId,
  multiline = false,
}: {
  label: string;
  rows: TypedValue[];
  setRows: (rows: TypedValue[]) => void;
  inputType: string;
  testId: string;
  /** Postal addresses span several lines; a single-line input would eat them. */
  multiline?: boolean;
}) => {
  const { t } = useTranslation();
  const typeOptions = TYPE_CHOICES.map((choice) => ({
    label: t(`contacts.types.${choice}`),
    value: choice,
  }));

  return (
    <div className="contact-form__rows">
      {rows.map((row, index) => {
        const onValueChange = (
          event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
        ) => {
          const next = [...rows];
          next[index] = { ...row, value: event.target.value };
          setRows(next);
        };
        return (
          <div className="contact-form__row" key={index}>
            {multiline ? (
              <TextArea
                label={label}
                value={row.value}
                rows={2}
                fullWidth
                data-testid={`${testId}-${index}`}
                onChange={onValueChange}
              />
            ) : (
              <Input
                label={label}
                type={inputType}
                value={row.value}
                fullWidth
                data-testid={`${testId}-${index}`}
                onChange={onValueChange}
              />
            )}
            <Select
              label={t("contacts.form.type")}
              options={typeOptions}
              value={row.type || "work"}
              clearable={false}
              onChange={(event) => {
                const next = [...rows];
                next[index] = { ...row, type: String(event.target.value) };
                setRows(next);
              }}
            />
            <Button
              size="small"
              color="neutral"
              variant="tertiary"
              aria-label={t("contacts.form.removeRow")}
              icon={<XMark />}
              onClick={() => setRows(rows.filter((_, i) => i !== index))}
            />
          </div>
        );
      })}
      <Button
        size="small"
        color="neutral"
        variant="tertiary"
        icon={<Plus />}
        onClick={() => setRows([...rows, { type: "work", value: "" }])}
      >
        {label}
      </Button>
    </div>
  );
};

interface ContactFormModalProps {
  isOpen: boolean;
  contact: Contact | null; // null = create
  onClose: () => void;
  /**
   * When set, the form edits a *local copy* of this read-only contact: the
   * overlay is created only on save (so cancelling leaves no trace), then
   * the edits are applied to it. `contact` carries the merged source values
   * to pre-fill the form.
   */
  forkSourceId?: string;
  onSaved?: (contact: Contact) => void;
}

export const ContactFormModal = ({
  isOpen,
  contact,
  onClose,
  forkSourceId,
  onSaved,
}: ContactFormModalProps) => {
  const { t } = useTranslation();
  const { config } = useConfig();
  const createContact = useCreateContact();
  const updateContact = useUpdateContact();
  const forkContact = useForkContact();
  const { labels } = useLabels();
  const photoInputRef = useRef<HTMLInputElement>(null);

  const initial: Partial<Contact> = contact ?? {};
  const [grammaticalGender, setGrammaticalGender] = useState<string>(
    initial.grammatical_gender ?? "",
  );
  const [namePrefix, setNamePrefix] = useState(initial.name_prefix ?? "");
  const [givenName, setGivenName] = useState(initial.given_name ?? "");
  const [middleName, setMiddleName] = useState(initial.middle_name ?? "");
  const [familyName, setFamilyName] = useState(initial.family_name ?? "");
  const [nameSuffix, setNameSuffix] = useState(initial.name_suffix ?? "");
  const [nickname, setNickname] = useState(initial.nickname ?? "");
  // The displayed name follows the parts until the user writes their own
  // (or the card came with one of its own, e.g. "Mairie de Brigny").
  const [fullName, setFullName] = useState(initial.full_name ?? "");
  const [isFullNameCustom, setIsFullNameCustom] = useState(
    !!initial.full_name && initial.full_name !== composedNameOf(initial),
  );
  const [showMoreNames, setShowMoreNames] = useState(
    !!(initial.name_prefix || initial.middle_name || initial.name_suffix || initial.nickname),
  );
  const composedName = composedNameOf({
    given_name: givenName,
    middle_name: middleName,
    family_name: familyName,
    name_suffix: nameSuffix,
  });
  // An emptied custom name falls back to the parts, as the server does.
  const displayedName = (isFullNameCustom ? fullName.trim() : "") || composedName;
  const [company, setCompany] = useState(contact?.company ?? "");
  const [department, setDepartment] = useState(contact?.department ?? "");
  const [jobTitle, setJobTitle] = useState(contact?.job_title ?? "");
  const [notes, setNotes] = useState(contact?.notes ?? "");
  const [photo, setPhoto] = useState(contact?.photo ?? "");
  const [labelIds, setLabelIds] = useState<string[]>(contact?.label_ids ?? []);
  const [emails, setEmails] = useState<TypedValue[]>(
    contact?.emails?.length ? contact.emails : [{ type: "work", value: "" }],
  );
  const [phones, setPhones] = useState<TypedValue[]>(
    contact?.phones?.length ? contact.phones : [{ type: "work", value: "" }],
  );
  const [addresses, setAddresses] = useState<TypedValue[]>(contact?.addresses ?? []);
  const [urls, setUrls] = useState<TypedValue[]>(contact?.urls ?? []);
  const [error, setError] = useState<string | null>(null);

  const isEdit = contact !== null;
  const isPending = createContact.isPending || updateContact.isPending || forkContact.isPending;
  const { user } = useAuth();
  const [shareOnCreate, setShareOnCreate] = useState(false);
  // Editing a shared contact itself (administrators), or creating one.
  const isOrgContact = isEdit ? !!contact.is_org_contact && !forkSourceId : shareOnCreate;

  const grammaticalGenderOptions = [
    { label: t("contacts.form.grammaticalGenders.masculine"), value: "masculine" },
    { label: t("contacts.form.grammaticalGenders.feminine"), value: "feminine" },
  ];

  const handlePhotoChange = async (file: File | null) => {
    if (!file) return;
    try {
      setPhoto(await resizePhoto(file));
    } catch {
      setError(t("contacts.form.photoError"));
    }
  };

  const handleSave = async () => {
    setError(null);
    const payload: ContactPayload = {
      // An organization contact carries no labels (they are per user).
      ...(isOrgContact ? {} : { label_ids: labelIds }),
      ...(isEdit || forkSourceId ? {} : { is_org_contact: isOrgContact }),
      grammatical_gender: (grammaticalGender || "") as ContactPayload["grammatical_gender"],
      name_prefix: namePrefix.trim(),
      given_name: givenName.trim(),
      middle_name: middleName.trim(),
      family_name: familyName.trim(),
      name_suffix: nameSuffix.trim(),
      nickname: nickname.trim(),
      full_name: displayedName,
      company: company.trim(),
      department: department.trim(),
      job_title: jobTitle.trim(),
      notes: notes.trim(),
      photo,
      emails: cleanRows(emails),
      phones: cleanRows(phones),
      addresses: cleanRows(addresses),
      urls: cleanRows(urls),
    };
    try {
      let saved;
      if (forkSourceId) {
        // Create the local overlay now (on save), then apply the edits.
        const overlay = await forkContact.mutateAsync({ id: forkSourceId });
        saved = await updateContact.mutateAsync({ id: overlay.data.id, data: payload });
      } else if (isEdit) {
        saved = await updateContact.mutateAsync({ id: contact.id, data: payload });
      } else {
        saved = await createContact.mutateAsync({ data: payload });
      }
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
      title={isEdit ? t("contacts.form.editTitle") : t("contacts.form.createTitle")}
      rightActions={
        <>
          <Button color="neutral" variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            color="brand"
            onClick={() => void handleSave()}
            disabled={isPending || !displayedName}
            data-testid="save-contact-button"
          >
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="contact-form">
        {error && <Alert type={VariantType.ERROR}>{error}</Alert>}
        {!isEdit && !forkSourceId && user?.is_admin && (
          <Checkbox
            label={t("contacts.form.shareWithOrg")}
            text={t("contacts.form.shareWithOrgHint")}
            checked={shareOnCreate}
            data-testid="contact-org-checkbox"
            onChange={(event) => setShareOnCreate(event.target.checked)}
          />
        )}
        {isEdit && isOrgContact && (
          <Alert type={VariantType.INFO}>{t("contacts.form.editingShared")}</Alert>
        )}

        <div className="contact-form__photo">
          <ContactAvatar
            name={displayedName || "?"}
            givenName={givenName}
            familyName={familyName}
            photo={photo}
            size="lg"
          />
          <input
            ref={photoInputRef}
            type="file"
            accept="image/*"
            style={{ display: "none" }}
            data-testid="photo-input"
            onChange={(event) => void handlePhotoChange(event.target.files?.[0] ?? null)}
          />
          <div className="contact-form__photo__actions">
            <Button
              size="small"
              color="neutral"
              variant="secondary"
              icon={<ImageAdd />}
              onClick={() => photoInputRef.current?.click()}
            >
              {photo ? t("contacts.form.changePhoto") : t("contacts.form.addPhoto")}
            </Button>
            {photo && (
              <Button size="small" color="neutral" variant="tertiary" onClick={() => setPhoto("")}>
                {t("contacts.form.removePhoto")}
              </Button>
            )}
          </div>
        </div>

        <h3 className="contact-form__section">{t("contacts.form.identity")}</h3>
        <div className="contact-form__names">
          <Select
            className="contact-form__grammatical-gender"
            label={t("contacts.form.grammaticalGender")}
            options={grammaticalGenderOptions}
            value={grammaticalGender || undefined}
            onChange={(event) =>
              setGrammaticalGender(event.target.value ? String(event.target.value) : "")
            }
          />
          <Input
            label={t("contacts.form.givenName")}
            value={givenName}
            data-testid="contact-given-name"
            onChange={(event) => setGivenName(event.target.value)}
          />
          <Input
            label={t("contacts.form.familyName")}
            value={familyName}
            data-testid="contact-family-name"
            onChange={(event) => setFamilyName(event.target.value)}
          />
        </div>
        {showMoreNames ? (
          <>
            <div className="contact-form__names">
              <Input
                label={t("contacts.form.namePrefix")}
                value={namePrefix}
                data-testid="contact-name-prefix"
                onChange={(event) => setNamePrefix(event.target.value)}
              />
              <Input
                label={t("contacts.form.middleName")}
                value={middleName}
                data-testid="contact-middle-name"
                onChange={(event) => setMiddleName(event.target.value)}
              />
              <Input
                label={t("contacts.form.nameSuffix")}
                value={nameSuffix}
                data-testid="contact-name-suffix"
                onChange={(event) => setNameSuffix(event.target.value)}
              />
            </div>
            <Input
              label={t("contacts.form.nickname")}
              value={nickname}
              fullWidth
              data-testid="contact-nickname"
              onChange={(event) => setNickname(event.target.value)}
            />
          </>
        ) : (
          <div>
            <Button
              size="small"
              color="neutral"
              variant="tertiary"
              icon={<Plus />}
              onClick={() => setShowMoreNames(true)}
              data-testid="contact-more-names"
            >
              {t("contacts.form.moreNames")}
            </Button>
          </div>
        )}
        <Input
          label={`${t("contacts.form.fullName")} *`}
          text={t("contacts.form.fullNameHint")}
          value={isFullNameCustom ? fullName : composedName}
          fullWidth
          data-testid="contact-full-name"
          onChange={(event) => {
            setFullName(event.target.value);
            setIsFullNameCustom(true);
          }}
          // Left empty: follow the parts again.
          onBlur={() => setIsFullNameCustom(!!fullName.trim())}
        />

        <h3 className="contact-form__section">{t("contacts.form.coordinates")}</h3>
        <TypedValueRows
          label={t("contacts.form.email")}
          rows={emails}
          setRows={setEmails}
          inputType="email"
          testId="contact-email"
        />
        <TypedValueRows
          label={t("contacts.form.phone")}
          rows={phones}
          setRows={setPhones}
          inputType="tel"
          testId="contact-phone"
        />
        <TypedValueRows
          label={t("contacts.form.address")}
          rows={addresses}
          setRows={setAddresses}
          inputType="text"
          testId="contact-address"
          multiline
        />
        <TypedValueRows
          label={t("contacts.form.url")}
          rows={urls}
          setRows={setUrls}
          inputType="url"
          testId="contact-url"
        />

        <h3 className="contact-form__section">{t("contacts.form.organization")}</h3>
        <div className="contact-form__names">
          <Input
            label={t("contacts.form.company")}
            value={company}
            data-testid="contact-company"
            onChange={(event) => setCompany(event.target.value)}
          />
          <Input
            label={t("contacts.form.department")}
            value={department}
            onChange={(event) => setDepartment(event.target.value)}
          />
        </div>
        <Input
          label={t("contacts.form.jobTitle")}
          value={jobTitle}
          fullWidth
          onChange={(event) => setJobTitle(event.target.value)}
        />

        {!isOrgContact && <h3 className="contact-form__section">{t("contacts.labels.title")}</h3>}
        <div className="contact-form__labels" hidden={isOrgContact}>
          {labels.map((label) => {
            const isActive = labelIds.includes(label.id);
            return (
              <button
                key={label.id}
                type="button"
                className="label-toggle"
                aria-pressed={isActive}
                style={
                  isActive
                    ? { color: "white", backgroundColor: label.color }
                    : { color: label.color, backgroundColor: `${label.color}1A` }
                }
                onClick={() =>
                  setLabelIds(
                    isActive ? labelIds.filter((id) => id !== label.id) : [...labelIds, label.id],
                  )
                }
              >
                {label.name}
              </button>
            );
          })}
        </div>

        <TextArea
          label={t("contacts.form.notes")}
          value={notes}
          fullWidth
          onChange={(event) => setNotes(event.target.value)}
        />

        <p className="contact-form__rgpd">
          {t("contacts.form.rgpdNotice", {
            days: config?.TRASHBIN_CUTOFF_DAYS ?? 30,
          })}
        </p>
      </div>
    </Modal>
  );
};
