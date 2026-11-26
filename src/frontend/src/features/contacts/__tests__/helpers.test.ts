import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { colorOf, initialsOf } from "../components/avatar/ContactAvatar";
import { deleteModalPrefix } from "../components/contact-detail/DeleteContactModal";
import { filtersFromSearch, parseContactsSearch } from "../navigation";
import {
  honorificOf,
  composedNameOf,
  contactMatches,
  sectionLetterOf,
  sortNameOf,
  splitAroundFamilyName,
  structuredNameOf,
  websiteHref,
} from "../utils";

describe("contactMatches", () => {
  const alban = {
    display_name: "Alban Siou",
    emails: [{ value: "alban.siou@brigny.example" }],
    phones: [{ value: "+33 6 39 98 63 09" }],
  };

  it("matches every word of the name or emails, accents folded", () => {
    expect(contactMatches(alban, "siou alban")).toBe(true);
    expect(contactMatches(alban, "brigny")).toBe(true);
    expect(contactMatches({ ...alban, display_name: "Élodie" }, "elodie")).toBe(true);
    expect(contactMatches(alban, "siou talot")).toBe(false);
  });

  it("matches a phone number however it is spaced, trunk 0 optional", () => {
    expect(contactMatches(alban, "39 98 63")).toBe(true);
    expect(contactMatches(alban, "06 39 98 63 09")).toBe(true);
    expect(contactMatches(alban, "+33639")).toBe(true);
    expect(contactMatches(alban, "07 39")).toBe(false);
  });
});

describe("name helpers", () => {
  const helene = {
    name_prefix: "Dr",
    given_name: "Hélène",
    middle_name: " Marie ",
    family_name: "de La Tour",
    name_suffix: "",
  };

  it("composes the parts like the server fills a blank full name", () => {
    expect(composedNameOf(helene)).toBe("Hélène Marie de La Tour");
    expect(structuredNameOf(helene)).toBe("Dr Hélène Marie de La Tour");
    expect(composedNameOf({})).toBe("");
  });

  it("files a contact under its family name, else its displayed name", () => {
    expect(sortNameOf({ family_name: "Siou", display_name: "Alban Siou" })).toBe("Siou");
    expect(sortNameOf({ family_name: "", display_name: "Médiathèque" })).toBe("Médiathèque");
  });

  it("splits the displayed name around the family name", () => {
    expect(splitAroundFamilyName("Hélène de La Tour", "de La Tour")).toEqual([
      "Hélène ",
      "de La Tour",
      "",
    ]);
    expect(splitAroundFamilyName("Claire D. (annuaire)", "Dubois")).toBeNull();
    expect(splitAroundFamilyName("Médiathèque", "")).toBeNull();
  });
});

describe("parseContactsSearch", () => {
  it("keeps known non-empty values only", () => {
    expect(
      parseContactsSearch({ q: "jean", label: "l1", favorites: "1", sort: "desc", other: "x" }),
    ).toEqual({ q: "jean", label: "l1", favorites: "1", sort: "desc" });
  });

  it("drops empty or unexpected values", () => {
    expect(parseContactsSearch({ q: "", favorites: "true", sort: "asc", label: 3 })).toEqual({
      q: undefined,
      label: undefined,
      favorites: undefined,
      sort: undefined,
    });
  });
});

describe("filtersFromSearch", () => {
  it("maps URL flags to API filters", () => {
    expect(filtersFromSearch({ q: "a", favorites: "1", sort: "desc" })).toEqual({
      q: "a",
      label: undefined,
      favorites: true,
      sortDesc: true,
    });
    expect(filtersFromSearch({})).toEqual({
      q: undefined,
      label: undefined,
      favorites: false,
      sortDesc: false,
    });
  });
});

describe("sectionLetterOf", () => {
  it("files accented names under their base letter", () => {
    expect(sectionLetterOf("Élodie Garnier")).toBe("E");
    expect(sectionLetterOf("  inès")).toBe("I");
  });

  it("files names not starting with a letter under #", () => {
    expect(sectionLetterOf("(annuaire)")).toBe("#");
    expect(sectionLetterOf("42 rue")).toBe("#");
    expect(sectionLetterOf("")).toBe("#");
  });
});

describe("initialsOf", () => {
  it("prefers the first and last name when both are known", () => {
    expect(initialsOf("Hélène de La Tour", "Hélène", "de La Tour")).toBe("HD");
    expect(initialsOf("Claire D. (annuaire)", "Claire", "Dubois")).toBe("CD");
    expect(initialsOf("Madonna", "Madonna", "")).toBe("MA");
  });

  it("uses first and last word initials", () => {
    expect(initialsOf("Jeanne Martin")).toBe("JM");
    expect(initialsOf("Jeanne Marie Martin")).toBe("JM");
  });

  it("uses two letters for single-word names", () => {
    expect(initialsOf("Jeanne")).toBe("JE");
  });

  it("falls back to ? for empty names", () => {
    expect(initialsOf("  ")).toBe("?");
  });
});

describe("colorOf", () => {
  it("is deterministic", () => {
    expect(colorOf("Jeanne Martin")).toBe(colorOf("Jeanne Martin"));
  });

  it("returns a hex color", () => {
    expect(colorOf("Anyone")).toMatch(/^#[0-9A-Fa-f]{6}$/);
  });
});

describe("honorificOf", () => {
  // A t() stub that echoes the key, so we assert the key lookup itself.
  const t = ((key: string) => key) as TFunction;

  it("maps genders to the translated honorific keys", () => {
    expect(honorificOf(t, { grammatical_gender: "masculine" })).toBe(
      "contacts.form.grammaticalGenders.masculine",
    );
    expect(honorificOf(t, { grammatical_gender: "feminine" })).toBe(
      "contacts.form.grammaticalGenders.feminine",
    );
    expect(honorificOf(t, { grammatical_gender: "neuter" })).toBe("");
    expect(honorificOf(t, { grammatical_gender: "" })).toBe("");
    expect(honorificOf(t, {})).toBe("");
  });
});

describe("websiteHref", () => {
  it("prepends https:// to scheme-less values", () => {
    expect(websiteHref("example.com")).toBe("https://example.com");
    expect(websiteHref("example.com/a?b=c")).toBe("https://example.com/a?b=c");
  });

  it("keeps values that already have a scheme", () => {
    expect(websiteHref("https://example.com")).toBe("https://example.com");
    expect(websiteHref("http://example.com")).toBe("http://example.com");
  });
});

describe("deleteModalPrefix", () => {
  it("uses the permanent wording in the trash", () => {
    expect(deleteModalPrefix({ overlay_of: null }, true)).toBe("contacts.deleteForeverModal");
    expect(deleteModalPrefix({ overlay_of: "abc" }, true)).toBe("contacts.deleteForeverModal");
  });

  it("uses the revert wording for overlays and trash wording otherwise", () => {
    expect(deleteModalPrefix({ overlay_of: "abc" })).toBe("contacts.revertModal");
    expect(deleteModalPrefix({ overlay_of: null })).toBe("contacts.deleteModal");
  });
});
