import type { ReactNode } from "react";

/**
 * Settings tab header: muted resource count on the left (left empty while
 * loading), "New ..." action on the right.
 */
export const ResourceSectionHeader = ({
  label,
  action,
}: {
  label?: ReactNode;
  action?: ReactNode;
}) => (
  <header className="settings__section-header">
    <strong className="settings__section-count">{label}</strong>
    {action}
  </header>
);
