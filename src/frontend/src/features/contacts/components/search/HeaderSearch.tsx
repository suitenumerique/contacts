import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { Button } from "@gouvfr-lasuite/ui-components";
import { XMark, Zoom } from "@gouvfr-lasuite/ui-components/icons";
import { useCmdK } from "@/hooks/useCmdK";

const DEBOUNCE_MS = 250;

/** Header search: filters the contact list through the `q` URL param. */
export const HeaderSearch = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const query = useRouterState({
    select: (state) => (state.location.search as { q?: string }).q ?? "",
  });
  const [value, setValue] = useState(query);
  const [syncedQuery, setSyncedQuery] = useState(query);
  const lastSent = useRef(query);
  const inputRef = useRef<HTMLInputElement>(null);

  useCmdK(
    useCallback(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, []),
  );

  // Follow the URL when it changes elsewhere (history, nav links).
  if (query !== syncedQuery) {
    setSyncedQuery(query);
    setValue(query);
    lastSent.current = query;
  }

  const submit = (next: string) => {
    const trimmed = next.trim();
    if (trimmed === lastSent.current) return;
    lastSent.current = trimmed;
    void navigate({
      to: "/contacts/{-$contactId}",
      params: { contactId: undefined },
      search: (prev) => ({ ...prev, q: trimmed || undefined }),
      // Typing refines one search: keep a single history entry for it.
      replace: !!query && !!trimmed,
    });
  };

  useEffect(() => {
    const handle = setTimeout(() => submit(value), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [value]);

  const reset = () => {
    setValue("");
    submit("");
  };

  return (
    <div className="header-search" role="search">
      <label className="header-search__label" htmlFor="header-search">
        <Zoom size={18} aria-hidden="true" />
        <span className="c__offscreen">{t("contacts.search.placeholder")}</span>
      </label>
      <input
        id="header-search"
        ref={inputRef}
        className="header-search__input"
        type="search"
        aria-keyshortcuts="Meta+K Control+K"
        autoComplete="off"
        placeholder={t("contacts.search.placeholder")}
        value={value}
        data-testid="search-input"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") submit(value);
          if (event.key === "Escape") reset();
        }}
      />
      {value && (
        <Button
          color="neutral"
          variant="tertiary"
          size="small"
          icon={<XMark />}
          title={t("contacts.search.clear")}
          aria-label={t("contacts.search.clear")}
          onClick={reset}
        />
      )}
    </div>
  );
};
