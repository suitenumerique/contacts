import type { ContactLabel } from "../../types";

/** Colored "libellé" chip: the label color on a light tint of itself. */
export const LabelBadge = ({ label }: { label: Pick<ContactLabel, "name" | "color"> }) => (
  <span className="label-badge" style={{ color: label.color, backgroundColor: `${label.color}1F` }}>
    {label.name}
  </span>
);
