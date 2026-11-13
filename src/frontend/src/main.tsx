import "./styles/globals.scss";
import "./features/i18n/initI18n";

import { createRoot } from "react-dom/client";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { parseSearch, stringifySearch } from "./features/app/searchParams";
import { AppErrorScreen } from "./features/errors/AppErrorScreen";
import { NotFoundScreen } from "./features/errors/NotFoundScreen";
import { routeTree } from "./routes.gen";

const router = createRouter({
  routeTree,
  scrollRestoration: false,
  defaultPreload: false,
  defaultErrorComponent: AppErrorScreen,
  defaultNotFoundComponent: NotFoundScreen,
  // The not-found page has its own layout. The root route must render it,
  // or it shows inside the layout of the application.
  notFoundMode: "root",
  parseSearch,
  stringifySearch,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const container = document.getElementById("root");
if (!container) throw new Error("#root element not found in index.html");

createRoot(container).render(<RouterProvider router={router} />);
