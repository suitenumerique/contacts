/**
 * Middle column: title and count, a filter/sort/more toolbar, then the
 * contacts grouped by initial letter, with infinite scroll. In the trash,
 * rows offer restore and delete-forever instead.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "@tanstack/react-router";
import { Button, DropdownMenu, Spinner } from "@gouvfr-lasuite/ui-components";
import {
  ArrowDownWideNarrow,
  ArrowUpNarrowWide,
  Download,
  Filter,
  More,
  Restore,
  Trash,
  Upload,
} from "@gouvfr-lasuite/ui-components/icons";
import clsx from "clsx";
import { baseApiUrl } from "@/features/api/utils";
import { useConfig } from "@/features/config/ConfigProvider";
import { useContactsList, useLabels, useRestoreContact } from "../../hooks/useContactsQueries";
import { useContactsModals } from "../../modals";
import { filtersFromSearch, type ContactsSearch } from "../../navigation";
import type { ContactListItem } from "../../types";
import { sectionLetterOf, sortNameOf, splitAroundFamilyName } from "../../utils";
import { ContactAvatar } from "../avatar/ContactAvatar";
import { LabelBadge } from "../labels/LabelBadge";

const exportUrl = (format: "vcf" | "csv") =>
  new URL(`contacts/export-${format}/`, baseApiUrl("1.0")).href;

const ListToolbar = ({ search }: { search: ContactsSearch }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { openModal } = useContactsModals();
  const { labels } = useLabels();
  const [filterOpen, setFilterOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const sortDesc = search.sort === "desc";
  const isFiltered = !!(search.label || search.favorites);

  const setSearch = (patch: Partial<ContactsSearch>) =>
    void navigate({
      to: "/contacts/{-$contactId}",
      params: (prev) => prev,
      search: (prev) => ({ ...prev, ...patch }),
    });

  return (
    <div className="list-toolbar">
      <DropdownMenu
        isOpen={filterOpen}
        onOpenChange={setFilterOpen}
        options={[
          {
            label: t("contacts.list.filterAll"),
            isChecked: !isFiltered,
            callback: () => setSearch({ label: undefined, favorites: undefined }),
          },
          {
            label: t("contacts.nav.favorites"),
            isChecked: !!search.favorites,
            callback: () => setSearch({ label: undefined, favorites: "1" }),
            showSeparator: labels.length > 0,
          },
          ...labels.map((label) => ({
            label: label.name,
            isChecked: search.label === label.id,
            callback: () => setSearch({ label: label.id, favorites: undefined }),
          })),
        ]}
      >
        <Button
          size="small"
          color="neutral"
          variant="tertiary"
          className={clsx({ "list-toolbar__button--active": isFiltered })}
          aria-label={t("contacts.list.filter")}
          icon={<Filter />}
          onClick={() => setFilterOpen(!filterOpen)}
          data-testid="list-filter"
        />
      </DropdownMenu>
      <Button
        size="small"
        color="neutral"
        variant="tertiary"
        aria-label={t(sortDesc ? "contacts.list.sortAsc" : "contacts.list.sortDesc")}
        title={t(sortDesc ? "contacts.list.sortAsc" : "contacts.list.sortDesc")}
        icon={sortDesc ? <ArrowUpNarrowWide /> : <ArrowDownWideNarrow />}
        onClick={() => setSearch({ sort: sortDesc ? undefined : "desc" })}
        data-testid="list-sort"
      />
      <DropdownMenu
        isOpen={moreOpen}
        onOpenChange={setMoreOpen}
        options={[
          {
            label: t("contacts.new.import"),
            icon: <Upload />,
            callback: () => openModal({ kind: "import" }),
          },
          {
            label: t("contacts.export.vcf"),
            icon: <Download />,
            callback: () => window.location.assign(exportUrl("vcf")),
          },
          {
            label: t("contacts.export.csv"),
            icon: <Download />,
            callback: () => window.location.assign(exportUrl("csv")),
          },
        ]}
      >
        <Button
          size="small"
          color="neutral"
          variant="tertiary"
          aria-label={t("contacts.list.more")}
          icon={<More />}
          onClick={() => setMoreOpen(!moreOpen)}
          data-testid="list-more"
        />
      </DropdownMenu>
    </div>
  );
};

/** The name, with the family name the list is sorted on in bold. */
const ContactRowName = ({ contact }: { contact: ContactListItem }) => {
  const parts = splitAroundFamilyName(contact.display_name, contact.family_name);
  return (
    <span className="contact-row__name">
      {parts ? (
        <>
          {parts[0]}
          <strong>{parts[1]}</strong>
          {parts[2]}
        </>
      ) : (
        contact.display_name
      )}
    </span>
  );
};

interface ContactListProps {
  mode: "contacts" | "trash";
  search: ContactsSearch;
  selectedId?: string;
}

export const ContactList = ({ mode, search, selectedId }: ContactListProps) => {
  const { t } = useTranslation();
  const { config } = useConfig();
  const { openModal } = useContactsModals();
  const isTrash = mode === "trash";
  const filters = isTrash ? { trash: true } : filtersFromSearch(search);
  const { contacts, count, isLoading, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useContactsList(filters);
  const { labels } = useLabels();
  const restoreContact = useRestoreContact();

  const labelsById = useMemo(() => new Map(labels.map((label) => [label.id, label])), [labels]);

  const title = isTrash
    ? t("contacts.nav.trash")
    : search.q
      ? t("contacts.list.searchResults")
      : search.favorites
        ? t("contacts.nav.favorites")
        : search.label
          ? (labelsById.get(search.label)?.name ?? t("contacts.list.title"))
          : t("contacts.list.title");

  // The server sorts, so letter buckets follow the order across pages.
  const sections = useMemo(() => {
    if (isTrash || search.q) {
      return [{ letter: "", items: contacts }];
    }
    const buckets = new Map<string, ContactListItem[]>();
    for (const contact of contacts) {
      const letter = sectionLetterOf(sortNameOf(contact));
      const bucket = buckets.get(letter);
      if (bucket) bucket.push(contact);
      else buckets.set(letter, [contact]);
    }
    return [...buckets.entries()].map(([letter, items]) => ({ letter, items }));
  }, [contacts, isTrash, search.q]);

  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || !hasNextPage) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !isFetchingNextPage) {
        void fetchNextPage();
      }
    });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, contacts.length]);

  const emptyMessage = isTrash
    ? t("contacts.trash.empty")
    : search.q
      ? t("contacts.list.noResults")
      : search.label || search.favorites
        ? t("contacts.list.noMatch")
        : t("contacts.list.empty");

  return (
    <div className="contact-list" data-testid="contact-list">
      <header className="pane-header">
        <div className="pane-header__titles">
          <h1 className="pane-header__title">{title}</h1>
          <span className="pane-header__subtitle">
            {isLoading ? "\u00a0" : t("contacts.list.count", { count })}
          </span>
        </div>
        {!isTrash && <ListToolbar search={search} />}
      </header>

      {isTrash && (
        <p className="contact-list__notice">
          {t("contacts.trash.retention", { days: config.TRASHBIN_CUTOFF_DAYS ?? 30 })}
        </p>
      )}

      {isLoading ? (
        <div className="pane-empty">
          <Spinner />
        </div>
      ) : contacts.length === 0 ? (
        <div className="pane-empty">
          <p>{emptyMessage}</p>
        </div>
      ) : (
        <div className="contact-list__sections">
          {sections.map((section) => (
            <div key={section.letter || "_"} role="list" aria-label={section.letter || title}>
              {section.letter && (
                <div className="contact-list__letter" aria-hidden="true">
                  {section.letter}
                </div>
              )}
              {section.items.map((contact) => {
                const rowLabels = isTrash
                  ? []
                  : (contact.label_ids ?? []).flatMap((id) => labelsById.get(id) ?? []);
                return (
                  <div
                    key={contact.id}
                    role="listitem"
                    className={clsx("contact-row", {
                      "contact-row--selected": contact.id === selectedId,
                    })}
                  >
                    <Link
                      to={isTrash ? "/trash/{-$contactId}" : "/contacts/{-$contactId}"}
                      params={{ contactId: contact.id }}
                      search={(prev) => prev}
                      className="contact-row__link"
                      aria-current={contact.id === selectedId ? "true" : undefined}
                      data-testid={`contact-row-${contact.id}`}
                    >
                      <ContactAvatar
                        name={contact.display_name}
                        givenName={contact.given_name}
                        familyName={contact.family_name}
                        photo={contact.photo}
                      />
                      <ContactRowName contact={contact} />

                      {rowLabels.map((label) => (
                        <LabelBadge key={label.id} label={label} />
                      ))}
                    </Link>
                    {isTrash && (
                      <span className="contact-row__actions">
                        <Button
                          size="small"
                          color="neutral"
                          variant="tertiary"
                          aria-label={t("contacts.trash.restoreNamed", {
                            name: contact.display_name,
                          })}
                          title={t("contacts.trash.restore")}
                          icon={<Restore />}
                          data-testid={`restore-${contact.id}`}
                          onClick={() => restoreContact.mutate({ id: contact.id })}
                        />
                        <Button
                          size="small"
                          color="error"
                          variant="tertiary"
                          aria-label={t("contacts.trash.deleteForeverNamed", {
                            name: contact.display_name,
                          })}
                          title={t("contacts.trash.deleteForever")}
                          icon={<Trash />}
                          data-testid={`delete-forever-${contact.id}`}
                          onClick={() =>
                            openModal({ kind: "delete-contact", contact, permanent: true })
                          }
                        />
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
          <div ref={sentinelRef} className="contact-list__sentinel">
            {isFetchingNextPage && <Spinner size="sm" />}
          </div>
        </div>
      )}
    </div>
  );
};
