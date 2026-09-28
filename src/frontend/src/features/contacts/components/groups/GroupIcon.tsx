import clsx from "clsx";
import { PersoFilled } from "@gouvfr-lasuite/ui-components/icons";
import { colorOf } from "../avatar/ContactAvatar";

const GLYPH_SIZE = { xs: 10, sm: 16, lg: 36 } as const;

/** Rounded square tinted from the group name (groups have no color field). */
export const GroupIcon = ({
  name,
  size = "sm",
  brand = false,
}: {
  name: string;
  size?: keyof typeof GLYPH_SIZE;
  brand?: boolean;
}) => (
  <span
    className={clsx("group-icon", `group-icon--${size}`, { "group-icon--brand": brand })}
    style={brand ? undefined : { backgroundColor: colorOf(name) }}
    aria-hidden="true"
  >
    <PersoFilled size={GLYPH_SIZE[size]} />
  </span>
);
