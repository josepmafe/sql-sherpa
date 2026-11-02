import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Panel, usePanelRef } from "react-resizable-panels";
import { clampLayout } from "./layoutStorage";
import { COLLAPSED_TOLERANCE_PX, type PaneEntry, usePaneGroup } from "./PaneGroup";

type Props = {
  id: string;
  /** The DOM id, when several groups hold a pane of this `id` (it keys the stored layout). */
  domId?: string;
  /** Pixels (the width of a horizontal group's pane, the height of a vertical one's). Left out, the pane takes what the others leave. */
  defaultSize?: number;
  minSize: number;
  maxSize: number;
  /** Set when the pane can fold to a strip of this size. */
  collapsedSize?: number;
  children: ReactNode;
};

export function Pane({ id, domId, defaultSize, minSize, maxSize, collapsedSize, children }: Props) {
  const { latest, register, unregister, report, startCollapsed } = usePaneGroup();
  const panelRef = usePanelRef();
  const [stored] = useState(() => {
    const layout = latest();
    return layout && clampLayout(layout, { [id]: { min: minSize, max: maxSize } });
  });
  const startsCollapsed = collapsedSize !== undefined && stored?.collapsed[id] === true;
  // A pane without a default size takes what the others leave: a pixel size stored under another window width would not add up.
  const sized = defaultSize !== undefined;
  const openSize = sized ? (stored?.sizes[id] ?? defaultSize) : undefined;
  const entry = useRef<PaneEntry>({
    handle: () => panelRef.current,
    collapsedSize,
    lastOpenSize: openSize ?? minSize,
    sized,
  });

  useEffect(() => {
    entry.current.collapsedSize = collapsedSize;
    entry.current.sized = sized;
    register(id, entry.current);
    return () => unregister(id);
  }, [id, collapsedSize, sized, register, unregister]);

  useLayoutEffect(() => {
    if (startsCollapsed) startCollapsed(id);
  }, [id, startsCollapsed, startCollapsed]);

  return (
    <Panel
      id={domId ?? id}
      panelRef={panelRef}
      defaultSize={startsCollapsed ? collapsedSize : openSize}
      // A window resize changes the pane that takes what is left, not the sized ones (UI spec §6.4).
      groupResizeBehavior={sized ? "preserve-pixel-size" : undefined}
      minSize={minSize}
      maxSize={maxSize}
      collapsible={collapsedSize !== undefined}
      collapsedSize={collapsedSize}
      onResize={({ inPixels }) => {
        report(id, collapsedSize !== undefined && inPixels <= collapsedSize + COLLAPSED_TOLERANCE_PX);
      }}
    >
      {children}
    </Panel>
  );
}
