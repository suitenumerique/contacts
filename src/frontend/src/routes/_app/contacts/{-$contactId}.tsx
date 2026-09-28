import { createFileRoute } from "@tanstack/react-router";
import { parseContactsSearch } from "@/features/contacts/navigation";
import { ContactsPage } from "@/features/contacts/pages/ContactsPage";

const ContactsRoute = () => {
  const { contactId } = Route.useParams();
  const search = Route.useSearch();
  return <ContactsPage contactId={contactId} search={search} />;
};

export const Route = createFileRoute("/_app/contacts/{-$contactId}")({
  validateSearch: parseContactsSearch,
  component: ContactsRoute,
});
