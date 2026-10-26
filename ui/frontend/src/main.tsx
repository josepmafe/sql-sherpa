import "@mantine/core/styles.css";

import { type CSSVariablesResolver, MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { routes } from "./app/routes";

const queryClient = new QueryClient();
const router = createBrowserRouter(routes);
// Mantine's dimmed grey is 3.3:1 on white and 4.0:1 on its dark body, below WCAG AA's 4.5:1 for the small text that
// uses it (status lines, hints, data types). These are 4.8:1 and 5.9:1 (5.2:1 on a dark menu).
const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: { "--mantine-color-dimmed": "#6b7280" },
  dark: { "--mantine-color-dimmed": "#a0a0a0" },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* "auto" follows the OS light/dark setting; Mantine's default font stack is the system one (offline). */}
    <MantineProvider defaultColorScheme="auto" cssVariablesResolver={cssVariablesResolver}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
