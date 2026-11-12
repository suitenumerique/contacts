import { useEffect } from "react";

/**
 * Calls `callback` when the user presses Cmd+K or Ctrl+K.
 * The shortcut does nothing while a modal dialog is open.
 */
export const useCmdK = (callback: () => void) => {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== "k") return;
      if (!(event.metaKey || event.ctrlKey) || event.shiftKey || event.altKey) return;
      // The page behind a modal is not inert. Menus and popovers that have
      // the dialog role do not set `aria-modal`.
      if (document.querySelector('[aria-modal="true"]')) return;
      event.preventDefault();
      callback();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [callback]);
};
