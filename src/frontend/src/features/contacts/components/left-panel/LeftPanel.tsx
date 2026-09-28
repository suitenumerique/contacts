/**
 * Sidebar: "Nouveau" menu, then the address book, groups and favorites
 * trees, then the trash.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useRouterState } from "@tanstack/react-router";
import { Button, DropdownMenu } from "@gouvfr-lasuite/ui-components";
import {
  Plus,
  Shared,
  Star,
  Trash,
  Upload,
  User,
  UserAdd,
} from "@gouvfr-lasuite/ui-components/icons";
import { useLeftPanel } from "@/features/layouts/contexts/LeftPanelContext";
import {
  useContactsList,
  useFavoriteGroup,
  useGroups,
  useUnfavoriteGroup,
} from "../../hooks/useContactsQueries";
import { useContactsModals } from "../../modals";
import { ContactAvatar } from "../avatar/ContactAvatar";
import { GroupIcon } from "../groups/GroupIcon";
import { SidebarItem, SidebarTree } from "./SidebarItem";

const NewMenu = () => {
  const { t } = useTranslation();
  const { openModal } = useContactsModals();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <DropdownMenu
      isOpen={isOpen}
      onOpenChange={setIsOpen}
      options={[
        {
          label: t("contacts.new.contact"),
          icon: <UserAdd />,
          callback: () => openModal({ kind: "contact-form", contact: null }),
        },
        {
          label: t("contacts.new.group"),
          icon: <Shared />,
          callback: () => openModal({ kind: "group-form" }),
        },
        {
          label: t("contacts.new.import"),
          icon: <Upload />,
          callback: () => openModal({ kind: "import" }),
        },
      ]}
    >
      <Button icon={<Plus />} onClick={() => setIsOpen(!isOpen)} data-testid="new-button">
        {t("contacts.new.button")}
      </Button>
    </DropdownMenu>
  );
};

export const LeftPanel = () => {
  const { t } = useTranslation();
  const { setIsLeftPanelOpen } = useLeftPanel();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [groupsExpanded, setGroupsExpanded] = useState(false);
  const [favoritesExpanded, setFavoritesExpanded] = useState(true);
  const { openModal } = useContactsModals();

  const { groups } = useGroups();
  const { contacts: favoriteContacts } = useContactsList({ favorites: true });
  const favoriteGroups = groups.filter((group) => group.is_favorite);
  const favoriteGroup = useFavoriteGroup();
  const unfavoriteGroup = useUnfavoriteGroup();

  // Picking an entry closes the drawer on mobile.
  const onNavigate = () => setIsLeftPanelOpen(false);
  const isAt = (path: string) => pathname === path || pathname.startsWith(`${path}/`);

  const groupMenu = (group: (typeof groups)[number]) => [
    {
      label: group.is_favorite ? t("contacts.favorites.remove") : t("contacts.favorites.add"),
      callback: () =>
        group.is_favorite
          ? unfavoriteGroup.mutate({ id: group.id })
          : favoriteGroup.mutate({ id: group.id }),
    },
    ...(group.is_writable
      ? [
          {
            label: t("contacts.groups.edit"),
            callback: () => openModal({ kind: "group-form", group }),
          },
          {
            label: t("contacts.groups.delete"),
            callback: () => openModal({ kind: "delete-group", group }),
          },
        ]
      : []),
  ];

  // Favorites are shortcuts: only the Groups tree marks the open group.
  const groupItem = (group: (typeof groups)[number], markActive = true) => (
    <SidebarItem
      key={group.id}
      nested
      icon={<GroupIcon name={group.name} size="xs" />}
      label={group.name}
      link={{ to: "/groups/{-$groupId}", params: { groupId: group.id } }}
      active={markActive && pathname === `/groups/${group.id}`}
      onNavigate={onNavigate}
      menu={groupMenu(group)}
    />
  );

  return (
    <div className="sidebar">
      <div className="sidebar__actions">
        <NewMenu />
      </div>

      <nav className="sidebar__section" aria-label={t("contacts.sidebar.navigation")}>
        <SidebarItem
          icon={<User />}
          label={t("contacts.nav.contacts")}
          link={{ to: "/contacts/{-$contactId}", params: { contactId: undefined } }}
          active={isAt("/contacts")}
          onNavigate={onNavigate}
        />
        <SidebarTree
          icon={<Shared />}
          label={t("contacts.nav.groups")}
          link={{ to: "/groups/{-$groupId}", params: { groupId: undefined } }}
          active={
            isAt("/groups") &&
            !(groupsExpanded && groups.some((group) => pathname === `/groups/${group.id}`))
          }
          expanded={groupsExpanded}
          onToggle={() => setGroupsExpanded(!groupsExpanded)}
          onNavigate={onNavigate}
        >
          {groups.map((group) => groupItem(group))}
        </SidebarTree>
        <SidebarTree
          icon={<Star />}
          label={t("contacts.nav.favorites")}
          expanded={favoritesExpanded}
          onToggle={() => setFavoritesExpanded(!favoritesExpanded)}
        >
          {favoriteGroups.map((group) => groupItem(group, false))}
          {favoriteContacts.map((contact) => (
            <SidebarItem
              key={contact.id}
              nested
              icon={<ContactAvatar name={contact.display_name} photo={contact.photo} size="xs" />}
              label={contact.display_name}
              link={{ to: "/contacts/{-$contactId}", params: { contactId: contact.id } }}
              active={false}
              onNavigate={onNavigate}
            />
          ))}
          {favoriteGroups.length === 0 && favoriteContacts.length === 0 && (
            <p className="sidebar__empty">{t("contacts.favorites.empty")}</p>
          )}
        </SidebarTree>
      </nav>

      <nav className="sidebar__section" aria-label={t("contacts.nav.trash")}>
        <SidebarItem
          icon={<Trash />}
          label={t("contacts.nav.trash")}
          link={{ to: "/trash/{-$contactId}", params: { contactId: undefined } }}
          active={isAt("/trash")}
          onNavigate={onNavigate}
        />
      </nav>
    </div>
  );
};
