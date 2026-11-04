import type { RouteObject } from "react-router-dom";

import { ExplorerScreen } from "../screens/explorer/ExplorerScreen";
import { HomeScreen } from "../screens/home/HomeScreen";
import { LineageScreen } from "../screens/lineage/LineageScreen";
import { AppShell } from "./AppShell";
import { RouteError } from "./RouteError";

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { path: "/", element: <HomeScreen /> },
      { path: "/explorer", element: <ExplorerScreen /> },
      { path: "/lineage", element: <LineageScreen /> },
    ],
  },
];
