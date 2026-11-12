/**
 * Small display helpers shared across the contacts components.
 */

import type { TFunction } from "i18next";

/** Localized civility prefix ("Mr" / "M." / "Dhr.") for a contact. */
export const civilityOf = (t: TFunction, contact: { civility?: string | null }) =>
  contact.civility === "mr"
    ? t("contacts.form.civilities.mr")
    : contact.civility === "mrs"
      ? t("contacts.form.civilities.mrs")
      : "";

/**
 * Whether a contact may join a group: a shared group only takes contacts
 * the whole organization sees (the API refuses the others).
 */
export const canJoinGroup = (
  contact: { is_org_contact?: boolean; overlay_of?: string | null },
  group: { is_org_group?: boolean },
) => !group.is_org_group || !!contact.is_org_contact || !!contact.overlay_of;

type NameParts = {
  name_prefix?: string;
  given_name?: string;
  middle_name?: string;
  family_name?: string;
  name_suffix?: string;
};

/** Join name parts with single spaces, skipping the empty ones. */
export const composeName = (...parts: (string | undefined)[]) =>
  parts
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(" ");

/** The name the parts spell out, as the server fills a blank full name. */
export const composedNameOf = (contact: NameParts) =>
  composeName(contact.given_name, contact.middle_name, contact.family_name, contact.name_suffix);

/** Every structured part, title included, for the detail pane. */
export const structuredNameOf = (contact: NameParts) =>
  composeName(contact.name_prefix, composedNameOf(contact));

/** What the list files a contact under: its family name, else its name. */
export const sortNameOf = (contact: { family_name?: string; display_name: string }) =>
  contact.family_name?.trim() || contact.display_name;

/**
 * Split a displayed name around its family name, to emphasize the part
 * the list is sorted on. Null when the family name is not in it.
 */
export const splitAroundFamilyName = (displayName: string, familyName?: string) => {
  const family = familyName?.trim();
  const index = family ? displayName.lastIndexOf(family) : -1;
  if (!family || index < 0) return null;
  return [displayName.slice(0, index), family, displayName.slice(index + family.length)] as const;
};

const fold = (text: string) =>
  text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();

const PHONE_QUERY = /^\+?[\d\s().\-/]*\d[\d\s().\-/]*$/;

/**
 * Local search over loaded contacts: every word in the name or emails, or
 * for a phone-like query, its digits in a phone number. Phone numbers are
 * compared as typed, with the query's trunk "0" optional so "06 45" finds
 * "+33 6 45..."; the server search also reads international forms.
 */
export const contactMatches = (
  contact: { display_name: string; emails?: { value: string }[]; phones?: { value: string }[] },
  query: string,
) => {
  if (PHONE_QUERY.test(query.trim())) {
    const digits = query.replace(/\D/g, "");
    const national = digits.replace(/^0/, "");
    return (contact.phones ?? []).some((phone) => {
      const phoneDigits = phone.value.replace(/\D/g, "");
      return phoneDigits.includes(digits) || (!!national && phoneDigits.includes(national));
    });
  }
  const haystack = fold(
    [contact.display_name, ...(contact.emails ?? []).map((e) => e.value)].join(" "),
  );
  return fold(query)
    .split(/\s+/)
    .every((word) => haystack.includes(word));
};

const ISOLATE_INITIATORS = "\u2066\u2067\u2068";
const FIRST_STRONG_ISOLATE = "\u2068";
const POP_ISOLATE = "\u2069";
// The characters of the Unicode bidi class B (paragraph separator).
const PARAGRAPH_SEPARATORS = "\n\r\u001C\u001D\u001E\u0085\u2029";

/**
 * Wrap user text in a Unicode directional isolate, to quote it in a sentence.
 * A direction control character in the text (U+202E for example) then cannot
 * reorder the sentence around it. The isolates of the text itself are
 * balanced first, because a stray U+2069 would close the wrapper early.
 * Paragraph separators become spaces, because an isolate ends with its
 * paragraph.
 */
export const bidiIsolate = (text: string) => {
  let depth = 0;
  let balanced = "";
  for (const char of text) {
    if (PARAGRAPH_SEPARATORS.includes(char)) {
      balanced += " ";
      continue;
    }
    if (char === POP_ISOLATE) {
      if (depth === 0) continue;
      depth -= 1;
    } else if (ISOLATE_INITIATORS.includes(char)) {
      depth += 1;
    }
    balanced += char;
  }
  return FIRST_STRONG_ISOLATE + balanced + POP_ISOLATE.repeat(depth + 1);
};

/** Initial letter a contact is filed under, accents folded ("É" → "E"). */
export const sectionLetterOf = (name: string) => {
  const first = name
    .trim()
    .charAt(0)
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toUpperCase();
  return /\p{Lu}/u.test(first) ? first : "#";
};

/** href for a website field: scheme-less values ("example.com") would render
 *  as relative links, so they get an https:// prefix. */
export const websiteHref = (value: string) =>
  /^[a-z][a-z0-9+.-]*:/i.test(value) ? value : `https://${value}`;
