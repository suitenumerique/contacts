import { PropsWithChildren, useState } from "react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { Link, type LinkProps } from "@tanstack/react-router";
import { DropdownMenu, type DropdownMenuOption } from "@gouvfr-lasuite/ui-components";
import { ChevronDown, ChevronRight, More } from "@gouvfr-lasuite/ui-components/icons";

interface SidebarItemProps {
  icon: React.ReactNode;
  label: string;
  link: LinkProps;
  active: boolean;
  onNavigate?: () => void;
  /** Child rows sit one level deeper. */
  nested?: boolean;
  /** Actions revealed on hover behind a "…" button. */
  menu?: DropdownMenuOption[];
}

const ItemMenu = ({ label, options }: { label: string; options: DropdownMenuOption[] }) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <DropdownMenu isOpen={isOpen} onOpenChange={setIsOpen} options={options}>
      <button
        type="button"
        className={clsx("sidebar-item__menu", { "sidebar-item__menu--open": isOpen })}
        aria-label={t("contacts.sidebar.actionsFor", { name: label })}
        onClick={() => setIsOpen(!isOpen)}
      >
        <More size={16} />
      </button>
    </DropdownMenu>
  );
};

export const SidebarItem = ({
  icon,
  label,
  link,
  active,
  onNavigate,
  nested = false,
  menu,
}: SidebarItemProps) => (
  <div
    className={clsx("sidebar-item", {
      "sidebar-item--active": active,
      "sidebar-item--nested": nested,
    })}
  >
    <span className="sidebar-item__spacer" aria-hidden="true" />
    <Link
      {...link}
      className="sidebar-item__link"
      aria-current={active ? "page" : undefined}
      onClick={onNavigate}
    >
      <span className="sidebar-item__icon">{icon}</span>
      <span className="sidebar-item__label">{label}</span>
    </Link>
    {menu && menu.length > 0 && <ItemMenu label={label} options={menu} />}
  </div>
);

interface SidebarTreeProps {
  icon: React.ReactNode;
  label: string;
  expanded: boolean;
  onToggle: () => void;
  /** Without a link, the whole row toggles the tree. */
  link?: LinkProps;
  active?: boolean;
  onNavigate?: () => void;
}

export const SidebarTree = ({
  icon,
  label,
  expanded,
  onToggle,
  link,
  active = false,
  onNavigate,
  children,
}: PropsWithChildren<SidebarTreeProps>) => {
  const { t } = useTranslation();
  const chevron = expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />;
  const content = (
    <>
      <span className="sidebar-item__icon">{icon}</span>
      <span className="sidebar-item__label">{label}</span>
    </>
  );

  return (
    <div className="sidebar-tree">
      <div className={clsx("sidebar-item", { "sidebar-item--active": active })}>
        {link ? (
          <>
            <button
              type="button"
              className="sidebar-item__chevron"
              aria-expanded={expanded}
              aria-label={t(expanded ? "contacts.sidebar.collapse" : "contacts.sidebar.expand", {
                name: label,
              })}
              onClick={onToggle}
            >
              {chevron}
            </button>
            <Link
              {...link}
              className="sidebar-item__link"
              aria-current={active ? "page" : undefined}
              onClick={onNavigate}
            >
              {content}
            </Link>
          </>
        ) : (
          // Without a link the whole row is the toggle.
          <button
            type="button"
            className="sidebar-item__link sidebar-item__link--toggle"
            aria-expanded={expanded}
            onClick={onToggle}
          >
            <span className="sidebar-item__chevron" aria-hidden="true">
              {chevron}
            </span>
            {content}
          </button>
        )}
      </div>
      {expanded && (
        <div className="sidebar-tree__children" role="group" aria-label={label}>
          {children}
        </div>
      )}
    </div>
  );
};
