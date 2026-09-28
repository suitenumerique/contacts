import { createFileRoute } from "@tanstack/react-router";
import { GroupsPage } from "@/features/contacts/pages/GroupsPage";

const GroupsRoute = () => {
  const { groupId } = Route.useParams();
  return <GroupsPage groupId={groupId} />;
};

export const Route = createFileRoute("/_app/groups/{-$groupId}")({
  component: GroupsRoute,
});
