import { ContactDetail } from "../components/contact-detail/ContactDetail";
import { ContactList } from "../components/contact-list/ContactList";
import { TwoPane } from "../components/layout/TwoPane";

export const TrashPage = ({ contactId }: { contactId?: string }) => (
  <TwoPane
    hasSelection={!!contactId}
    list={<ContactList mode="trash" search={{}} selectedId={contactId} />}
    detail={<ContactDetail contactId={contactId} mode="trash" />}
  />
);
