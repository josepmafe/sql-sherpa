import { createContext, type ReactNode, useContext, useMemo, useRef, useState } from "react";
import { Group, type PanelImperativeHandle } from "react-resizable-panels";
import { loadLayout, type StoredLayout, writeLayout } from "./layoutStorage";

// Half a pixel absorbs the rounding of a size the library derives from a percentage.
export const COLLAPSED_TOLERANCE_PX = 0.5;

/** What a pane tells its group: how to reach the library's handle, its folded size (if it can fold) and the size to open back to. */
export type PaneEntry = {
  handle: () => PanelImperativeHandle | null;
  collapsedSize: number | undefined;
  lastOpenSize: number;
  /** Whether the pane declared a default size: only such a pane's size is stored (the others take what is left). */
  sized: boolean;
};

export type PaneGroupContext = {
  groupId: string;
  /** The layout stored for this group, as written (a pane clamps its own entry). */
  stored: StoredLayout | undefined;
  collapsed: Readonly<Record<string, boolean>>;
  register: (paneId: string, entry: PaneEntry) => void;
  unregister: (paneId: string) => void;
  /** A pane's size changed: tracks its collapsed flag (the library folds a pane dragged below its minimum). */
  report: (paneId: string, collapsed: boolean) => void;
  /** A pane that starts folded from the stored layout. */
  startCollapsed: (paneId: string) => void;
  setCollapsed: (paneId: string, collapsed: boolean) => void;
  /** Saves the live sizes: for a change the library does not report as the user's (the double-click reset). */
  persistSoon: () => void;
};

const GroupContext = createContext<PaneGroupContext | undefined>(undefined);

export function usePaneGroup(): PaneGroupContext {
  const context = useContext(GroupContext);
  if (!context) throw new Error("a Pane must sit inside a PaneGroup");
  return context;
}

/** Whether pane `id` is folded, and a function that folds or opens it. Call it below the PaneGroup. */
export function usePaneCollapse(id: string): {
  collapsed: boolean;
  toggle: () => void;
} {
  const { collapsed, setCollapsed } = usePaneGroup();
  const isCollapsed = collapsed[id] === true;
  return {
    collapsed: isCollapsed,
    toggle: () => setCollapsed(id, !isCollapsed),
  };
}

type Props = {
  id: string;
  direction: "horizontal" | "vertical";
  children: ReactNode;
};

/** Panes and handles side by side (horizontal) or stacked (vertical). Sizes and collapsed flags persist under `id`. */
export function PaneGroup({ id, direction, children }: Props) {
  const panes = useRef(new Map<string, PaneEntry>());
  const [collapsed, setCollapsedState] = useState<Record<string, boolean>>({});
  const [stored] = useState(() => loadLayout(id));

  // The functions do not depend on `collapsed`, so a pane's effects that call them do not rerun when a flag changes.
  const actions = useMemo(() => {
    // Reads the live sizes: a pane at its folded size is folded, any other size is the one to open back to.
    const persist = (override?: { paneId: string; collapsed: boolean }): void => {
      const sizes: Record<string, number> = {};
      const flags: Record<string, boolean> = {};
      for (const [paneId, pane] of panes.current) {
        const size = pane.handle()?.getSize().inPixels;
        const folded =
          paneId === override?.paneId
            ? override.collapsed
            : pane.collapsedSize !== undefined &&
              size !== undefined &&
              size <= pane.collapsedSize + COLLAPSED_TOLERANCE_PX;
        // A pane the library has not yet opened still reads its strip size: that is not a size to open back to.
        const stripSize =
          pane.collapsedSize !== undefined && size !== undefined && size <= pane.collapsedSize + COLLAPSED_TOLERANCE_PX;
        if (!folded && !stripSize && size !== undefined) pane.lastOpenSize = size;
        if (pane.sized) sizes[paneId] = pane.lastOpenSize;
        if (pane.collapsedSize !== undefined) flags[paneId] = folded;
      }
      writeLayout(id, { sizes, collapsed: flags });
    };
    const setFlag = (paneId: string, flag: boolean): void =>
      setCollapsedState((current) => (current[paneId] === flag ? current : { ...current, [paneId]: flag }));
    return {
      persist,
      register: (paneId: string, entry: PaneEntry) => void panes.current.set(paneId, entry),
      unregister: (paneId: string) => void panes.current.delete(paneId),
      report: setFlag,
      startCollapsed: (paneId: string) => setFlag(paneId, true),
      setCollapsed: (paneId: string, next: boolean) => {
        const entry = panes.current.get(paneId);
        const handle = entry?.handle();
        if (!entry || !handle) return;
        if (next) {
          // Remember the size to open back to before the library folds the pane.
          const size = handle.getSize().inPixels;
          if (entry.collapsedSize === undefined || size > entry.collapsedSize + COLLAPSED_TOLERANCE_PX)
            entry.lastOpenSize = size;
          handle.collapse();
        } else {
          handle.expand();
          handle.resize(entry.lastOpenSize);
        }
        setFlag(paneId, next);
        // The library applies the new sizes after this call.
        queueMicrotask(() => persist({ paneId, collapsed: next }));
      },
    };
  }, [id]);

  const context = useMemo<PaneGroupContext>(
    () => ({
      groupId: id,
      stored,
      collapsed,
      register: actions.register,
      unregister: actions.unregister,
      report: actions.report,
      startCollapsed: actions.startCollapsed,
      setCollapsed: actions.setCollapsed,
      persistSoon: () => queueMicrotask(() => actions.persist()),
    }),
    [id, stored, collapsed, actions],
  );

  return (
    <GroupContext.Provider value={context}>
      <Group
        orientation={direction}
        id={id}
        onLayoutChanged={(_layout, meta) => {
          // A drag or an arrow key. A toggle persists for itself, and a constraint change is not the user's choice.
          // The library calls this before it has applied the new sizes, so the sizes are read a microtask later.
          if (meta.isUserInteraction) queueMicrotask(() => actions.persist());
        }}
        style={{ height: "100%", width: "100%" }}
      >
        {children}
      </Group>
    </GroupContext.Provider>
  );
}
