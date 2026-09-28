/**
 * Middle column of the groups page: every group the user can see, with
 * its member count. Opening one shows it in the detail pane.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { Button, DropdownMenu, Spinner } from "@gouvfr-lasuite/ui-components";
import { More, Plus, StarFilled } from "@gouvfr-lasuite/ui-components/icons";
import clsx from "clsx";
import { useGroups } from "../../hooks/useContactsQueries";
import { useContactsModals } from "../../modals";
import { GroupIcon } from "./GroupIcon";

export const GroupList = ({ selectedId }: { selectedId?: string }) => {
  const { t } = useTranslation();
  const { groups, isLoading } = useGroups();
  const { openModal } = useContactsModals();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="group-list" data-testid="group-list">
      <header className="pane-header">
        <div className="pane-header__titles">
          <h1 className="pane-header__title">
            {isLoading
              ? t("contacts.nav.groups")
              : t("contacts.groups.count", { count: groups.length })}
          </h1>
        </div>
        <DropdownMenu
          isOpen={menuOpen}
          onOpenChange={setMenuOpen}
          options={[
            {
              label: t("contacts.new.group"),
              icon: <Plus />,
              callback: () => openModal({ kind: "group-form" }),
            },
          ]}
        >
          <Button
            size="small"
            color="neutral"
            variant="tertiary"
            icon={<More />}
            aria-label={t("contacts.list.more")}
            onClick={() => setMenuOpen(!menuOpen)}
            data-testid="group-list-more"
          />
        </DropdownMenu>
      </header>

      {isLoading ? (
        <div className="pane-empty">
          <Spinner />
        </div>
      ) : groups.length === 0 ? (
        <div className="pane-empty">
          <p>{t("contacts.groups.empty")}</p>
          <Button
            variant="secondary"
            icon={<Plus />}
            onClick={() => openModal({ kind: "group-form" })}
          >
            {t("contacts.new.group")}
          </Button>
        </div>
      ) : (
        <div role="list" className="group-list__rows">
          {groups.map((group) => (
            <div
              key={group.id}
              role="listitem"
              className={clsx("group-row", { "group-row--selected": group.id === selectedId })}
            >
              <Link
                to="/groups/{-$groupId}"
                params={{ groupId: group.id }}
                className="group-row__link"
                aria-current={group.id === selectedId ? "true" : undefined}
                data-testid={`group-row-${group.id}`}
              >
                <GroupIcon name={group.name} />
                <span className="group-row__body">
                  <span className="group-row__name">
                    {group.name}
                    {group.is_favorite && (
                      <StarFilled
                        size={14}
                        className="group-row__star"
                        aria-label={t("contacts.favorites.is")}
                      />
                    )}
                  </span>
                  <span className="group-row__count">
                    {t("contacts.groups.memberCount", { count: group.members_count })}
                  </span>
                </span>
              </Link>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
