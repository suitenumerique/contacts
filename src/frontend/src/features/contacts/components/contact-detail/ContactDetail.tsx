/**
 * Right pane: the open contact as a card. Identity and quick actions on
 * top, then each coordinate with a copy button, then notes (editable in
 * place on the user's own cards). Card actions live in the "…" menu.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Link } from "@tanstack/react-router";
import {
  Button,
  DropdownMenu,
  Spinner,
  type DropdownMenuItem,
} from "@gouvfr-lasuite/ui-components";
import {
  ArrowLeft,
  Bubble,
  Building,
  Copy,
  Edit,
  Mail,
  Meet,
  More,
  Phone,
  Restore,
  Shared,
  Star,
  StarFilled,
  StarSlash,
  Trash,
  Undo,
} from "@gouvfr-lasuite/ui-components/icons";
import { useAuth } from "@/features/auth/Auth";
import { useConfig } from "@/features/config/ConfigProvider";
import { addSuccessToast } from "@/features/ui/components/toaster/Toaster";
import {
  useAddContactToGroup,
  useContact,
  useFavoriteContact,
  useGroups,
  useLabels,
  useRemoveContactFromGroup,
  useRestoreContact,
  useUnfavoriteContact,
  useUpdateContact,
} from "../../hooks/useContactsQueries";
import { useContactsModals } from "../../modals";
import type { Contact, TypedValue } from "../../types";
import { canJoinGroup, civilityOf, structuredNameOf, websiteHref } from "../../utils";
import { ContactAvatar } from "../avatar/ContactAvatar";
import { LabelBadge } from "../labels/LabelBadge";

const copyToClipboard = async (value: string, message: string) => {
  await navigator.clipboard.writeText(value);
  addSuccessToast(message);
};

const Field = ({ label, value, href }: { label: string; value: string; href?: string }) => {
  const { t } = useTranslation();
  return (
    <div className="contact-field">
      <div className="contact-field__body">
        <div className="contact-field__label">{label}</div>
        <div className="contact-field__value">
          {href ? (
            <a href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer">
              {value}
            </a>
          ) : (
            value
          )}
        </div>
      </div>
      <Button
        size="small"
        color="neutral"
        variant="tertiary"
        icon={<Copy />}
        aria-label={t("contacts.detail.copy", { label })}
        title={t("contacts.detail.copyTitle")}
        onClick={() => void copyToClipboard(value, t("contacts.detail.copied"))}
      />
    </div>
  );
};

const RoundAction = ({
  icon,
  label,
  href,
  newTab,
}: {
  icon: React.ReactNode;
  label: string;
  /** Without a target, the action shows disabled. */
  href?: string | null;
  newTab?: boolean;
}) =>
  href ? (
    <a
      className="round-action"
      href={href}
      aria-label={label}
      title={label}
      target={newTab ? "_blank" : undefined}
      rel={newTab ? "noreferrer" : undefined}
    >
      {icon}
    </a>
  ) : (
    <button type="button" className="round-action" aria-label={label} title={label} disabled>
      {icon}
    </button>
  );

const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

/** Notes field, saved on blur for the user's own cards. */
const Notes = ({ contact }: { contact: Contact }) => {
  const { t } = useTranslation();
  const updateContact = useUpdateContact();
  const [value, setValue] = useState(contact.notes ?? "");

  useEffect(() => setValue(contact.notes ?? ""), [contact.id, contact.notes]);

  if (!contact.is_writable || contact.deleted_at) {
    return contact.notes ? (
      <div className="contact-notes">
        <span className="contact-notes__label">{t("contacts.form.notes")}</span>
        <p className="contact-notes__text">{contact.notes}</p>
      </div>
    ) : null;
  }

  return (
    <label className="contact-notes">
      <span className="contact-notes__label">{t("contacts.form.notes")}</span>
      <textarea
        className="contact-notes__input"
        value={value}
        rows={4}
        placeholder={t("contacts.detail.notesPlaceholder")}
        data-testid="contact-notes"
        onChange={(event) => setValue(event.target.value)}
        onBlur={() => {
          if (value.trim() !== (contact.notes ?? "")) {
            updateContact.mutate(
              { id: contact.id, data: { notes: value.trim() } },
              { onSuccess: () => addSuccessToast(t("contacts.detail.notesSaved")) },
            );
          }
        }}
      />
    </label>
  );
};

const useCardMenu = (contact: Contact, isTrash: boolean): DropdownMenuItem[] => {
  const { t } = useTranslation();
  const { openModal } = useContactsModals();
  const { groups } = useGroups();
  const favorite = useFavoriteContact();
  const unfavorite = useUnfavoriteContact();
  const restore = useRestoreContact();
  const addToGroup = useAddContactToGroup();
  const removeFromGroup = useRemoveContactFromGroup();
  const { user } = useAuth();

  if (isTrash) {
    return [
      {
        label: t("contacts.trash.restore"),
        icon: <Restore />,
        callback: () => restore.mutate({ id: contact.id }),
      },
      {
        label: t("contacts.trash.deleteForever"),
        icon: <Trash />,
        variant: "danger",
        callback: () => openModal({ kind: "delete-contact", contact, permanent: true }),
      },
    ];
  }

  const memberOf = new Set(contact.group_ids ?? []);
  const writableGroups = groups.filter(
    (group) => group.is_writable && (memberOf.has(group.id) || canJoinGroup(contact, group)),
  );
  const isOverlay = !!contact.overlay_of;
  // An administrator may copy one of their own contacts to the organization.
  const canShare =
    !!user?.can_admin &&
    contact.is_writable &&
    !contact.is_org_contact &&
    !isOverlay &&
    !contact.source;

  return [
    {
      label: contact.is_favorite ? t("contacts.favorites.remove") : t("contacts.favorites.add"),
      icon: contact.is_favorite ? <StarSlash /> : <Star />,
      callback: () =>
        contact.is_favorite
          ? unfavorite.mutate({ id: contact.id })
          : favorite.mutate({ id: contact.id }),
    },
    contact.is_writable
      ? {
          label: t("contacts.detail.edit"),
          icon: <Edit />,
          callback: () => openModal({ kind: "contact-form", contact }),
        }
      : {
          label: t("contacts.detail.editLocally"),
          subText: t("contacts.detail.editLocallyHint"),
          icon: <Edit />,
          callback: () => openModal({ kind: "contact-form", contact, forkSourceId: contact.id }),
        },
    ...(canShare
      ? [
          {
            label: t("contacts.detail.shareWithOrg"),
            icon: <Building />,
            callback: () => openModal({ kind: "share-contact", contact }),
          },
        ]
      : []),
    ...(writableGroups.length > 0
      ? [
          {
            label: t("contacts.detail.groups"),
            icon: <Shared />,
            children: writableGroups.map((group) => ({
              label: group.name,
              isChecked: memberOf.has(group.id),
              keepOpen: true,
              callback: () =>
                memberOf.has(group.id)
                  ? removeFromGroup.mutate({ id: group.id, data: { contact_id: contact.id } })
                  : addToGroup.mutate({ id: group.id, data: { contact_id: contact.id } }),
            })),
          },
        ]
      : []),
    ...(contact.is_writable
      ? [
          { type: "separator" as const },
          isOverlay
            ? {
                label: t("contacts.detail.revert"),
                icon: <Undo />,
                callback: () => openModal({ kind: "delete-contact", contact, permanent: false }),
              }
            : {
                label: t("contacts.detail.delete"),
                icon: <Trash />,
                variant: "danger" as const,
                callback: () => openModal({ kind: "delete-contact", contact, permanent: false }),
              },
        ]
      : []),
  ];
};

/** Known types are translated; other vCard types (e.g. "voice") are hidden. */
const typeLabel = (t: TFunction, value: TypedValue) =>
  value.type ? t(`contacts.types.${value.type.toLowerCase()}`, { defaultValue: "" }) : "";

const joinLabel = (...parts: string[]) => parts.filter(Boolean).join(" · ");

const ContactCard = ({ contact, isTrash }: { contact: Contact; isTrash: boolean }) => {
  const { t } = useTranslation();
  const { config } = useConfig();
  const { labels } = useLabels();
  const { groups } = useGroups();
  const menu = useCardMenu(contact, isTrash);
  const [menuOpen, setMenuOpen] = useState(false);

  const name = [civilityOf(t, contact), contact.display_name].filter(Boolean).join(" ");
  // The parts are worth showing when the displayed name does not already
  // say it all (a title, a middle name, a free-form display name...).
  const structuredName = structuredNameOf(contact);
  const subtitle = [
    contact.job_title,
    [contact.department, contact.company].filter(Boolean).join(", "),
  ]
    .filter(Boolean)
    .join(" | ");
  const contactLabels = labels.filter((label) => (contact.label_ids ?? []).includes(label.id));
  const contactGroups = groups.filter((group) => (contact.group_ids ?? []).includes(group.id));
  const primaryEmail = contact.emails?.[0]?.value;
  const primaryPhone = contact.phones?.[0]?.value;
  const composeUrl = config.FRONTEND_MESSAGES_COMPOSE_URL;
  const mailHref = primaryEmail
    ? composeUrl
      ? composeUrl.replace("{email}", encodeURIComponent(primaryEmail))
      : `mailto:${primaryEmail}`
    : null;
  const provenance = contact.source
    ? t("contacts.detail.syncedNote", { source: contact.source })
    : contact.is_org_contact
      ? t("contacts.detail.orgNote")
      : contact.overlay_of
        ? t("contacts.detail.overlayNote")
        : null;

  return (
    <article className="card contact-card" aria-labelledby="contact-card-name">
      <div className="card__menu">
        <DropdownMenu isOpen={menuOpen} onOpenChange={setMenuOpen} options={menu}>
          <Button
            size="small"
            color="brand"
            variant="tertiary"
            icon={<More />}
            aria-label={t("contacts.detail.actions")}
            onClick={() => setMenuOpen(!menuOpen)}
            data-testid="contact-actions-button"
          />
        </DropdownMenu>
      </div>

      <div className="contact-card__identity">
        <ContactAvatar
          name={contact.display_name}
          givenName={contact.given_name}
          familyName={contact.family_name}
          photo={contact.photo}
          size="lg"
          brand
        />
        <h2 id="contact-card-name" className="contact-card__name">
          {name}
          {contact.is_favorite && (
            <StarFilled
              size={18}
              className="contact-card__star"
              aria-label={t("contacts.favorites.is")}
            />
          )}
        </h2>
        {subtitle && <p className="contact-card__subtitle">{subtitle}</p>}
        {contactLabels.length > 0 && (
          <div className="contact-card__labels">
            {contactLabels.map((label) => (
              <LabelBadge key={label.id} label={label} />
            ))}
          </div>
        )}
      </div>

      {!isTrash && (
        <div className="contact-card__actions">
          <RoundAction
            icon={<Phone />}
            label={t("contacts.detail.call")}
            href={primaryPhone ? telHref(primaryPhone) : null}
          />
          <RoundAction
            icon={<Mail />}
            label={t("contacts.detail.sendMail")}
            href={mailHref}
            newTab={!!composeUrl}
          />
          <RoundAction icon={<Bubble />} label={t("contacts.detail.chatSoon")} />
          <RoundAction icon={<Meet />} label={t("contacts.detail.videoSoon")} />
        </div>
      )}

      <section className="contact-card__data" aria-labelledby="contact-card-data">
        <h3 id="contact-card-data" className="contact-card__data-title">
          {contact.is_writable
            ? t("contacts.detail.yourData")
            : contact.source
              ? t("contacts.detail.syncedData")
              : t("contacts.detail.orgData")}
        </h3>
        {provenance && <p className="contact-card__provenance">{provenance}</p>}

        {structuredName && structuredName !== contact.display_name && (
          <Field label={t("contacts.detail.fullName")} value={structuredName} />
        )}
        {contact.nickname && <Field label={t("contacts.form.nickname")} value={contact.nickname} />}

        {(contact.phones ?? []).map((phone, index) => (
          <Field
            key={`phone-${index}`}
            label={joinLabel(t("contacts.detail.phone"), typeLabel(t, phone))}
            value={phone.value}
            href={telHref(phone.value)}
          />
        ))}
        {(contact.emails ?? []).map((email, index) => (
          <Field
            key={`email-${index}`}
            label={joinLabel(t("contacts.detail.email"), typeLabel(t, email))}
            value={email.value}
            href={`mailto:${email.value}`}
          />
        ))}
        {(contact.addresses ?? []).map((address, index) => (
          <Field
            key={`address-${index}`}
            label={joinLabel(t("contacts.detail.address"), typeLabel(t, address))}
            value={address.value}
          />
        ))}
        {(contact.urls ?? []).map((url, index) => (
          <Field
            key={`url-${index}`}
            label={joinLabel(t("contacts.detail.website"), typeLabel(t, url))}
            value={url.value}
            href={websiteHref(url.value)}
          />
        ))}
        {contactGroups.length > 0 && (
          <div className="contact-field">
            <div className="contact-field__body">
              <div className="contact-field__label">{t("contacts.detail.groups")}</div>
              <div className="contact-card__groups">
                {contactGroups.map((group) => (
                  <Link
                    key={group.id}
                    to="/groups/{-$groupId}"
                    params={{ groupId: group.id }}
                    className="contact-card__group"
                  >
                    {group.name}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}
      </section>

      <Notes contact={contact} />
    </article>
  );
};

export const ContactDetail = ({
  contactId,
  mode,
}: {
  contactId?: string;
  mode: "contacts" | "trash";
}) => {
  const { t } = useTranslation();
  const { contact, isLoading, isError } = useContact(contactId);
  // The contact decides, not the route: a trashed contact can be open at
  // the /contacts URL, and a delete on it is permanent.
  const isTrash = !!contact?.deleted_at;

  const shareContact = async (card: Contact) => {
    const text = [card.display_name, card.emails?.[0]?.value, card.phones?.[0]?.value]
      .filter(Boolean)
      .join("\n");
    if (navigator.share) {
      try {
        await navigator.share({ title: card.display_name, text });
      } catch {
        // Share sheet dismissed.
      }
      return;
    }
    await copyToClipboard(text, t("contacts.detail.shareCopied"));
  };

  if (!contactId) {
    return (
      <div className="pane-empty pane-empty--detail" data-testid="contact-detail-empty">
        <p>{t("contacts.detail.emptyState")}</p>
      </div>
    );
  }

  return (
    <div className="detail" data-testid="contact-detail">
      <div className="detail__toolbar">
        <Link
          to={mode === "trash" ? "/trash/{-$contactId}" : "/contacts/{-$contactId}"}
          params={{ contactId: undefined }}
          search={(prev) => prev}
          className="detail__back"
          aria-label={t("contacts.detail.back")}
        >
          <ArrowLeft />
        </Link>
        {contact && !isTrash && (
          <Button color="neutral" variant="bordered" onClick={() => void shareContact(contact)}>
            {t("contacts.detail.share")}
          </Button>
        )}
      </div>
      {isError ? (
        <div className="pane-empty">
          <p>{t("contacts.detail.loadError")}</p>
        </div>
      ) : isLoading || !contact ? (
        <div className="pane-empty">
          <Spinner />
        </div>
      ) : (
        <ContactCard contact={contact} isTrash={isTrash} />
      )}
    </div>
  );
};
