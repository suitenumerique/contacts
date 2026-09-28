/**
 * Initials avatar with a deterministic color per name, or the contact's
 * photo when one is set (data: URI or https URL).
 */

import clsx from "clsx";

const AVATAR_COLORS = [
  "#4B6BF0",
  "#A05A4B",
  "#2F7FA2",
  "#3F8A5C",
  "#C03A5A",
  "#8B5CC4",
  "#B36300",
  "#00786D",
];

const firstLetter = (text?: string) => text?.match(/\p{L}/u)?.[0] ?? "";

/**
 * Two letters, like other address books: first and last name when both are
 * known ("Hélène de La Tour" gives "HD"), else from the displayed name.
 */
export const initialsOf = (name: string, givenName?: string, familyName?: string) => {
  const given = firstLetter(givenName);
  const family = firstLetter(familyName);
  if (given && family) return (given + family).toUpperCase();
  // Letter-leading words only, so "(annuaire)" or "D." don't leak punctuation.
  const words = name
    .trim()
    .split(/\s+/)
    .map((word) => word.replace(/[^\p{L}]/gu, ""))
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
};

export const colorOf = (name: string) => {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) % 100_000;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
};

export type AvatarSize = "xs" | "sm" | "lg";

interface ContactAvatarProps {
  name: string;
  givenName?: string;
  familyName?: string;
  photo?: string;
  size?: AvatarSize;
  /** Use the brand color instead of the per-name one (the open card). */
  brand?: boolean;
}

export const ContactAvatar = ({
  name,
  givenName,
  familyName,
  photo,
  size = "sm",
  brand = false,
}: ContactAvatarProps) => {
  const className = clsx("contact-avatar", `contact-avatar--${size}`);
  if (photo) {
    return <img className={className} src={photo} alt="" aria-hidden="true" />;
  }
  return (
    <span
      className={clsx(className, { "contact-avatar--brand": brand })}
      style={brand ? undefined : { backgroundColor: colorOf(name) }}
      aria-hidden="true"
    >
      {size !== "xs" && initialsOf(name, givenName, familyName)}
    </span>
  );
};
