import { createFileRoute } from "@tanstack/react-router";
import { TrashPage } from "@/features/contacts/pages/TrashPage";

const TrashRoute = () => {
  const { contactId } = Route.useParams();
  return <TrashPage contactId={contactId} />;
};

export const Route = createFileRoute("/_app/trash/{-$contactId}")({
  component: TrashRoute,
});
