/**
 * Right pane of the groups page: the group card with its members, a
 * member search, and (on the user's own groups) adding and removing
 * members.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { Button, DropdownMenu, Spinner } from "@gouvfr-lasuite/ui-components";
import {
  ArrowLeft,
  ChevronDown,
  Edit,
  More,
  Plus,
  Star,
  StarSlash,
  Trash,
  UserSearch,
} from "@gouvfr-lasuite/ui-components/icons";
import {
  useAddContactToGroup,
  useContactsList,
  useFavoriteGroup,
  useGroupContacts,
  useGroups,
  useRemoveContactFromGroup,
  useUnfavoriteGroup,
} from "../../hooks/useContactsQueries";
import { useContactsModals } from "../../modals";
import type { ContactGroup, ContactListItem } from "../../types";
import { canJoinGroup, contactMatches } from "../../utils";
import { ContactAvatar } from "../avatar/ContactAvatar";
import { GroupIcon } from "./GroupIcon";

const MAX_CANDIDATES = 5;

const MemberRole = ({ group, member }: { group: ContactGroup; member: ContactListItem }) => {
  const { t } = useTranslation();
  const removeFromGroup = useRemoveContactFromGroup();
  const [isOpen, setIsOpen] = useState(false);

  if (!group.is_writable) return null;

  return (
    <DropdownMenu
      isOpen={isOpen}
      onOpenChange={setIsOpen}
      options={[
        {
          label: t("contacts.groups.removeMember"),
          icon: <Trash />,
          variant: "danger",
          callback: () => removeFromGroup.mutate({ id: group.id, data: { contact_id: member.id } }),
        },
      ]}
    >
      <button
        type="button"
        className="member-row__role"
        aria-label={t("contacts.groups.memberActions", { name: member.display_name })}
        onClick={() => setIsOpen(!isOpen)}
      >
        {t("contacts.groups.roleMember")}
        <ChevronDown size={16} />
      </button>
    </DropdownMenu>
  );
};

const GroupCard = ({ group }: { group: ContactGroup }) => {
  const { t, i18n } = useTranslation();
  const { openModal } = useContactsModals();
  const favorite = useFavoriteGroup();
  const unfavorite = useUnfavoriteGroup();
  const addToGroup = useAddContactToGroup();
  const { contacts: members, isLoading } = useGroupContacts(group.id);
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setQuery("");
  }, [group.id]);

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), 200);
    return () => clearTimeout(handle);
  }, [query]);

  const { contacts: searchResults } = useContactsList(
    { q: debouncedQuery },
    { enabled: group.is_writable && !!debouncedQuery },
  );
  const memberIds = useMemo(() => new Set(members.map((member) => member.id)), [members]);
  const visibleMembers = query.trim()
    ? members.filter((member) => contactMatches(member, query.trim()))
    : members;
  const candidates =
    group.is_writable && debouncedQuery
      ? searchResults
          .filter((contact) => !memberIds.has(contact.id) && canJoinGroup(contact, group))
          .slice(0, MAX_CANDIDATES)
      : [];

  const formatDate = (value: string) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: "long" }).format(new Date(value));

  return (
    <article className="card group-card" aria-labelledby="group-card-name">
      <div className="card__menu">
        <DropdownMenu
          isOpen={menuOpen}
          onOpenChange={setMenuOpen}
          options={[
            {
              label: group.is_favorite
                ? t("contacts.favorites.remove")
                : t("contacts.favorites.add"),
              icon: group.is_favorite ? <StarSlash /> : <Star />,
              callback: () =>
                group.is_favorite
                  ? unfavorite.mutate({ id: group.id })
                  : favorite.mutate({ id: group.id }),
            },
            ...(group.is_writable
              ? [
                  {
                    label: t("contacts.groups.edit"),
                    icon: <Edit />,
                    callback: () => openModal({ kind: "group-form", group }),
                  },
                  {
                    label: t("contacts.groups.delete"),
                    icon: <Trash />,
                    variant: "danger" as const,
                    callback: () => openModal({ kind: "delete-group", group }),
                  },
                ]
              : []),
          ]}
        >
          <Button
            size="small"
            color="brand"
            variant="tertiary"
            icon={<More />}
            aria-label={t("contacts.groups.actions")}
            onClick={() => setMenuOpen(!menuOpen)}
            data-testid="group-actions-button"
          />
        </DropdownMenu>
      </div>

      <div className="group-card__header">
        <GroupIcon name={group.name} size="lg" brand />
        <div className="group-card__heading">
          <h2 id="group-card-name" className="group-card__name">
            {group.name}
          </h2>
          {group.description && <p className="group-card__description">{group.description}</p>}
          {!group.is_writable && (
            <p className="group-card__provenance">
              {group.source
                ? t("contacts.groups.syncedNote", { source: group.source })
                : t("contacts.groups.orgNote")}
            </p>
          )}
          {group.is_writable && group.is_org_group && (
            <p className="group-card__provenance">{t("contacts.groups.sharedNote")}</p>
          )}
        </div>
      </div>

      <div className="group-card__meta">
        <div>
          <p className="group-card__count">
            {t("contacts.groups.memberCount", { count: group.members_count })}
          </p>
          <p className="group-card__dates">
            {t("contacts.groups.dates", {
              created: formatDate(group.created_at),
              updated: formatDate(group.updated_at),
            })}
          </p>
        </div>
        {group.is_writable && (
          <Button
            size="small"
            color="brand"
            variant="secondary"
            icon={<Plus />}
            onClick={() => inputRef.current?.focus()}
            data-testid="group-add-member"
          >
            {t("contacts.groups.addMember")}
          </Button>
        )}
      </div>

      <div className="member-search">
        <UserSearch size={20} aria-hidden="true" />
        <input
          ref={inputRef}
          type="search"
          className="member-search__input"
          placeholder={
            group.is_writable
              ? t("contacts.groups.searchOrAddMember")
              : t("contacts.groups.searchMember")
          }
          aria-label={t("contacts.groups.searchMember")}
          value={query}
          data-testid="group-member-search"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {candidates.length > 0 && (
        <div className="member-candidates" aria-label={t("contacts.groups.addCandidates")}>
          <p className="member-candidates__title">{t("contacts.groups.addCandidates")}</p>
          {candidates.map((contact) => (
            <button
              key={contact.id}
              type="button"
              className="member-candidates__item"
              data-testid={`group-add-${contact.id}`}
              onClick={() => {
                addToGroup.mutate({ id: group.id, data: { contact_id: contact.id } });
                setQuery("");
              }}
            >
              <ContactAvatar
                name={contact.display_name}
                givenName={contact.given_name}
                familyName={contact.family_name}
                photo={contact.photo}
              />
              <span className="member-row__body">
                <span className="member-row__name">{contact.display_name}</span>
                {contact.emails?.[0]?.value && (
                  <span className="member-row__email">{contact.emails[0].value}</span>
                )}
              </span>
              <Plus size={20} />
            </button>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="pane-empty">
          <Spinner />
        </div>
      ) : (
        <ul className="member-list">
          {visibleMembers.map((member) => (
            <li key={member.id} className="member-row">
              <Link
                to="/contacts/{-$contactId}"
                params={{ contactId: member.id }}
                className="member-row__link"
              >
                <ContactAvatar
                  name={member.display_name}
                  givenName={member.given_name}
                  familyName={member.family_name}
                  photo={member.photo}
                />
                <span className="member-row__body">
                  <span className="member-row__name">{member.display_name}</span>
                  {member.emails?.[0]?.value && (
                    <span className="member-row__email">{member.emails[0].value}</span>
                  )}
                </span>
              </Link>
              <MemberRole group={group} member={member} />
            </li>
          ))}
          {members.length === 0 && (
            <li className="member-list__empty">{t("contacts.groups.noMembers")}</li>
          )}
          {members.length > 0 && visibleMembers.length === 0 && (
            <li className="member-list__empty">{t("contacts.groups.noMemberMatch")}</li>
          )}
        </ul>
      )}
    </article>
  );
};

export const GroupDetail = ({ groupId }: { groupId?: string }) => {
  const { t } = useTranslation();
  const { groups, isLoading } = useGroups();
  const group = groups.find((candidate) => candidate.id === groupId);

  if (!groupId) {
    return (
      <div className="pane-empty pane-empty--detail" data-testid="group-detail-empty">
        <p>{t("contacts.groups.selectGroup")}</p>
      </div>
    );
  }

  return (
    <div className="detail" data-testid="group-detail">
      <div className="detail__toolbar">
        <Link
          to="/groups/{-$groupId}"
          params={{ groupId: undefined }}
          className="detail__back"
          aria-label={t("contacts.detail.back")}
        >
          <ArrowLeft />
        </Link>
      </div>
      {group ? (
        <GroupCard group={group} />
      ) : (
        <div className="pane-empty">
          {isLoading ? <Spinner /> : <p>{t("contacts.groups.notFound")}</p>}
        </div>
      )}
    </div>
  );
};
