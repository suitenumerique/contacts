import { useEffect } from "react";

// react-modal marks open modals with `--after-open` and mounts each one in a
// portal appended directly under <body>.
const OPEN_MODAL_SELECTOR = ".ReactModal__Content--after-open";

/**
 * When a modal stacked on top of this one closes, react-modal sometimes
 * leaves focus on <body>, which also breaks Escape. While `isOpen`, move focus
 * back into the topmost open modal in that case, and only in that case.
 * Ported from suitenumerique/messages.
 */
export const useRestoreFocusOnNestedModalClose = (isOpen: boolean) => {
  useEffect(() => {
    if (!isOpen) {
      return;
    }

    let openModalCount = document.querySelectorAll(OPEN_MODAL_SELECTOR).length;
    let rafId = 0;

    const observer = new MutationObserver(() => {
      const previousCount = openModalCount;
      openModalCount = document.querySelectorAll(OPEN_MODAL_SELECTOR).length;
      if (openModalCount >= previousCount || openModalCount === 0) {
        return;
      }

      // Let react-modal restore focus first, then step in if it did not.
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const openModals = document.querySelectorAll<HTMLElement>(OPEN_MODAL_SELECTOR);
        const activeElement = document.activeElement;
        if (
          activeElement &&
          Array.from(openModals).some((modal) => modal.contains(activeElement))
        ) {
          return;
        }
        openModals[openModals.length - 1]?.focus({ preventScroll: true });
      });
    });

    observer.observe(document.body, { childList: true });

    return () => {
      observer.disconnect();
      cancelAnimationFrame(rafId);
    };
  }, [isOpen]);
};
