import { createFileRoute, Outlet } from "@tanstack/react-router";
import { Auth } from "@/features/auth/Auth";
import { AppLayout } from "@/features/layouts/components/app/AppLayout";

export const Route = createFileRoute("/_app")({
  component: () => (
    <Auth redirect>
      <AppLayout>
        <Outlet />
      </AppLayout>
    </Auth>
  ),
});
