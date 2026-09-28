import { GroupDetail } from "../components/groups/GroupDetail";
import { GroupList } from "../components/groups/GroupList";
import { TwoPane } from "../components/layout/TwoPane";

export const GroupsPage = ({ groupId }: { groupId?: string }) => (
  <TwoPane
    hasSelection={!!groupId}
    list={<GroupList selectedId={groupId} />}
    detail={<GroupDetail groupId={groupId} />}
  />
);
