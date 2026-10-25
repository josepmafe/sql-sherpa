import { Navigate, type RouteObject } from "react-router-dom";

import { ExplorerScreen } from "../screens/explorer/ExplorerScreen";
import { AppShell } from "./AppShell";

// `/` goes to the Explorer until Home arrives (ui-3).
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <Navigate to="/explorer" replace /> },
      { path: "/explorer", element: <ExplorerScreen /> },
    ],
  },
];
