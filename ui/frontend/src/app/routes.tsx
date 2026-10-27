import { Navigate, type RouteObject } from "react-router-dom";

import { ExplorerScreen } from "../screens/explorer/ExplorerScreen";
import { LineageScreen } from "../screens/lineage/LineageScreen";
import { AppShell } from "./AppShell";
import { RouteError } from "./RouteError";

// `/` goes to the Explorer until Home arrives (ui-3).
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { path: "/", element: <Navigate to="/explorer" replace /> },
      { path: "/explorer", element: <ExplorerScreen /> },
      { path: "/lineage", element: <LineageScreen /> },
    ],
  },
];
