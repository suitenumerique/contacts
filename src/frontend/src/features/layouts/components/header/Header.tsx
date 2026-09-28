import { LanguagePicker, useResponsive } from "@gouvfr-lasuite/ui-components";
import { Link } from "@tanstack/react-router";
import { useAuth } from "@/features/auth/Auth";
import { useTranslation } from "react-i18next";
import { fetchAPI } from "@/features/api/fetchApi";
import { LANGUAGES } from "@/features/i18n/conf";
import { Gaufre } from "@/features/ui/components/gaufre/Gaufre";
import { ContactsLogo } from "@/features/ui/components/logo";
import { UserProfile } from "@/features/ui/components/user/UserProfile";

export const HeaderIcon = () => (
  <Link to="/" className="contacts__header__home">
    <ContactsLogo />
  </Link>
);

/** Suite switcher and user menu. On tablet they move to the sidebar footer. */
export const HeaderRight = () => {
  const { isTablet } = useResponsive();

  if (isTablet) {
    return null;
  }

  return (
    <>
      <Gaufre />
      <UserProfile />
    </>
  );
};

export const LanguagePickerUserMenu = () => {
  const { i18n } = useTranslation();
  const { user, refreshUser } = useAuth();
  // The backend language is the source of truth, synced into i18next by Auth.
  // i18next may report "en-US" or "en": match Django's "en-us" on the base.
  const active = (user?.language ?? i18n.language).toLowerCase();
  const current =
    LANGUAGES.find((language) => language.value === active)?.value ??
    LANGUAGES.find((language) => language.value.split("-")[0] === active.split("-")[0])?.value;

  const onChange = (value: string) => {
    void i18n.changeLanguage(value);
    if (user) {
      void fetchAPI(`users/${user.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ language: value }),
      }).then(() => refreshUser?.());
    }
  };

  return (
    <LanguagePicker
      languages={LANGUAGES.map((language) => ({
        ...language,
        isChecked: current === language.value,
      }))}
      size="small"
      onChange={onChange}
      compact
    />
  );
};
