import { EditorView } from "@codemirror/view";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult, screen } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { createMemoryRouter, MemoryRouter, type RouteObject, RouterProvider } from "react-router-dom";

type Options = { path?: string; queryClient?: QueryClient };

/** Mantine in test mode, a MemoryRouter at `path` and a QueryClient that never retries. `rerender` keeps them. */
export function renderWithProviders(ui: ReactElement, { path = "/", queryClient }: Options = {}): RenderResult {
  const client = queryClient ?? createTestQueryClient();
  function Providers({ children }: { children: ReactNode }) {
    return (
      <MantineProvider env="test">
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
        </QueryClientProvider>
      </MantineProvider>
    );
  }
  return render(ui, { wrapper: Providers });
}

/** The app's routes in a memory router at `path`: a RouterProvider cannot sit inside a MemoryRouter. */
export function renderRoutes(routes: RouteObject[], { path = "/", queryClient }: Options = {}): RenderResult {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <MantineProvider env="test">
      <QueryClientProvider client={queryClient ?? createTestQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

export function createTestQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

/** A hook wrapper that keeps one QueryClient for every render. */
export function queryClientWrapper(queryClient: QueryClient = createTestQueryClient()) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

export type Deferred<T = unknown> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
};

/** A promise the test settles by hand. */
export function deferred<T = unknown>(): Deferred<T> {
  let resolve!: Deferred<T>["resolve"];
  let reject!: Deferred<T>["reject"];
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** The first cell of every body row of the result grid on screen. */
export function firstColumn(): (string | null | undefined)[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => row.querySelector("td")?.textContent);
}

// Hidden worksheets stay mounted under a `display: none` box.
export function isShown(element: HTMLElement): boolean {
  return element.closest('[style*="display: none"]') === null;
}

/** Every worksheet's editor, hidden ones included. */
export function editors(): EditorView[] {
  return Array.from(document.querySelectorAll<HTMLElement>(".cm-editor")).map((element) => {
    const view = EditorView.findFromDOM(element);
    if (!view) throw new Error("no editor");
    return view;
  });
}

/** The editor of the worksheet on screen. */
export function shownEditor(): EditorView {
  const view = editors().find((candidate) => isShown(candidate.dom));
  if (!view) throw new Error("no shown editor");
  return view;
}

/** A screen at least 1280 px wide: `vi.spyOn(window, "matchMedia").mockImplementation(wideScreen)`. The setup's stub matches no query. */
export function wideScreen(query: string): MediaQueryList {
  return {
    matches: query === "(min-width: 80em)",
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  } as MediaQueryList;
}
