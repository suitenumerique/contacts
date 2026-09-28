import { describe, expect, it } from "vitest";
import translations from "../translations.json";

const flatKeys = (node: object, prefix = ""): string[] =>
  Object.entries(node).flatMap(([key, value]) =>
    value && typeof value === "object" ? flatKeys(value, `${prefix}${key}.`) : [`${prefix}${key}`],
  );

describe("translations", () => {
  const languages = Object.keys(translations) as (keyof typeof translations)[];
  const reference = flatKeys(translations.fr.translation).sort();

  it.each(languages)("%s has exactly the French keys", (language) => {
    expect(flatKeys(translations[language].translation).sort()).toEqual(reference);
  });
});
