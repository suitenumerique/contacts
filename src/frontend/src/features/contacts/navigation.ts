/**
 * URL state of the contacts pages. The router keeps search values as raw
 * strings (see main.tsx), so flags are "1" rather than booleans.
 */

import type { ContactFilters } from "./types";

export interface ContactsSearch {
  q?: string;
  label?: string;
  favorites?: "1";
  sort?: "desc";
}

const nonEmpty = (value: unknown) => (typeof value === "string" && value ? value : undefined);

export const parseContactsSearch = (search: Record<string, unknown>): ContactsSearch => ({
  q: nonEmpty(search.q),
  label: nonEmpty(search.label),
  favorites: search.favorites === "1" ? "1" : undefined,
  sort: search.sort === "desc" ? "desc" : undefined,
});

export const filtersFromSearch = (search: ContactsSearch): ContactFilters => ({
  q: search.q,
  label: search.label,
  favorites: search.favorites === "1",
  sortDesc: search.sort === "desc",
});
