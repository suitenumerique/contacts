import { ContactDetail } from "../components/contact-detail/ContactDetail";
import { ContactList } from "../components/contact-list/ContactList";
import { TwoPane } from "../components/layout/TwoPane";
import type { ContactsSearch } from "../navigation";

export const ContactsPage = ({
  contactId,
  search,
}: {
  contactId?: string;
  search: ContactsSearch;
}) => (
  <TwoPane
    hasSelection={!!contactId}
    list={<ContactList mode="contacts" search={search} selectedId={contactId} />}
    detail={<ContactDetail contactId={contactId} mode="contacts" />}
  />
);
