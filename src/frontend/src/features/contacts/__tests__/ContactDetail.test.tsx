import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CunninghamProvider, type DropdownMenuItem } from "@gouvfr-lasuite/ui-components";
import { ContactDetail } from "../components/contact-detail/ContactDetail";
import type { Contact } from "../types";

const mocks = vi.hoisted(() => ({
  contact: undefined as Contact | undefined,
  openModal: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}));

// The menu shows its options as buttons, so a test can read and click them
// without a popover.
vi.mock("@gouvfr-lasuite/ui-components", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@gouvfr-lasuite/ui-components")>()),
  DropdownMenu: ({ options, children }: { options: DropdownMenuItem[]; children: ReactNode }) => (
    <>
      {children}
      {options.map((option, index) =>
        "label" in option ? (
          <button key={index} data-testid="menu-option" onClick={() => option.callback?.()}>
            {option.label}
          </button>
        ) : null,
      )}
    </>
  ),
}));

vi.mock("@/features/auth/Auth", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("@/features/config/ConfigProvider", () => ({ useConfig: () => ({ config: {} }) }));
vi.mock("@/features/ui/components/toaster/Toaster", () => ({ addSuccessToast: vi.fn() }));
vi.mock("../modals", () => ({ useContactsModals: () => ({ openModal: mocks.openModal }) }));

vi.mock("../hooks/useContactsQueries", () => {
  const mutation = () => ({ mutate: vi.fn(), isPending: false });
  return {
    useContact: () => ({ contact: mocks.contact, isLoading: false, isError: false }),
    useLabels: () => ({ labels: [] }),
    useGroups: () => ({ groups: [] }),
    useFavoriteContact: mutation,
    useUnfavoriteContact: mutation,
    useRestoreContact: mutation,
    useUpdateContact: mutation,
    useAddContactToGroup: mutation,
    useRemoveContactFromGroup: mutation,
  };
});

const contact = (fields: Partial<Contact>) =>
  ({
    id: "c1",
    display_name: "Ada Lovelace",
    is_writable: true,
    phones: [{ value: "+33 6 12 34 56 78" }],
    ...fields,
  }) as Contact;

describe("ContactDetail on the contacts route", () => {
  let container: HTMLElement;
  let root: Root;

  const render = (value: Contact) => {
    mocks.contact = value;
    act(() =>
      root.render(
        <CunninghamProvider>
          <ContactDetail contactId={value.id} mode="contacts" />
        </CunninghamProvider>,
      ),
    );
  };
  const menuOptions = () => [
    ...container.querySelectorAll<HTMLElement>("[data-testid=menu-option]"),
  ];
  const menuLabels = () => menuOptions().map((option) => option.textContent);
  const clickOption = (label: string) =>
    act(() =>
      menuOptions()
        .find((option) => option.textContent === label)
        ?.click(),
    );

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.body.appendChild(document.createElement("div"));
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    mocks.openModal.mockReset();
  });

  it("shows a trashed contact as trashed", () => {
    const trashed = contact({ deleted_at: "2026-11-01T10:00:00Z" });
    render(trashed);

    expect(container.querySelector("[data-testid=trash-banner]")).not.toBeNull();
    expect(container.querySelector(".contact-card__actions")).toBeNull();
    expect(container.textContent).not.toContain("contacts.detail.share");
    expect(menuLabels()).toEqual(["contacts.trash.restore", "contacts.trash.deleteForever"]);

    clickOption("contacts.trash.deleteForever");
    expect(mocks.openModal).toHaveBeenCalledWith({
      kind: "delete-contact",
      contact: trashed,
      permanent: true,
    });
  });

  it("opens non-permanent delete confirmation for a live contact", () => {
    const live = contact({ deleted_at: null });
    render(live);

    expect(container.querySelector("[data-testid=trash-banner]")).toBeNull();
    expect(container.querySelector(".contact-card__actions")).not.toBeNull();
    expect(menuLabels()).not.toContain("contacts.trash.deleteForever");

    clickOption("contacts.detail.delete");
    expect(mocks.openModal).toHaveBeenCalledWith({
      kind: "delete-contact",
      contact: live,
      permanent: false,
    });
  });
});
