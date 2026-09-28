import { describe, expect, it } from "vitest";
import { parseSearch, stringifySearch } from "../searchParams";

describe("parseSearch", () => {
  it("keeps every value as a string", () => {
    expect(parseSearch("?q=1234&favorites=1&flag=true&label=abc")).toEqual({
      q: "1234",
      favorites: "1",
      flag: "true",
      label: "abc",
    });
  });

  it("decodes encoded values", () => {
    expect(parseSearch("q=%C3%A9lodie+garnier")).toEqual({ q: "élodie garnier" });
  });
});

describe("stringifySearch", () => {
  it("drops empty values and prefixes with ?", () => {
    expect(stringifySearch({ q: "06 12", label: undefined, sort: "", favorites: "1" })).toBe(
      "?q=06+12&favorites=1",
    );
    expect(stringifySearch({})).toBe("");
  });

  it("round-trips with parseSearch", () => {
    const search = { q: "1234", sort: "desc" };
    expect(parseSearch(stringifySearch(search))).toEqual(search);
  });
});
