import clsx from "clsx";
import { useTranslation } from "react-i18next";

interface ContactsLogoProps {
  /** "header": icon + app name; "icon": the icon alone, larger. */
  variant?: "header" | "icon";
}

export const ContactsLogo = ({ variant = "header" }: ContactsLogoProps) => {
  const { t } = useTranslation();

  return (
    <span className={clsx("contacts-logo", `contacts-logo--${variant}`)}>
      <img className="contacts-logo__icon" src="/logo.svg" alt="" aria-hidden="true" />
      {variant === "header" && <span className="contacts-logo__text">{t("app_title")}</span>}
    </span>
  );
};
