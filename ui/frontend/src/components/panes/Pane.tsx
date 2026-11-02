import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Panel, usePanelRef } from "react-resizable-panels";
import { readLayout } from "./layoutStorage";
import { COLLAPSED_TOLERANCE_PX, type PaneEntry, usePaneGroup } from "./PaneGroup";

type Props = {
  id: string;
  /** Pixels (the width of a horizontal group's pane, the height of a vertical one's). Left out, the pane takes what the others leave. */
  defaultSize?: number;
  minSize: number;
  maxSize: number;
  /** Set when the pane can fold to a strip of this size. */
  collapsedSize?: number;
  children: ReactNode;
};

export function Pane({ id, defaultSize, minSize, maxSize, collapsedSize, children }: Props) {
  const { groupId, register, unregister, report, startCollapsed } = usePaneGroup();
  const panelRef = usePanelRef();
  const [stored] = useState(() => readLayout(groupId, { [id]: { min: minSize, max: maxSize } }));
  const startsCollapsed = collapsedSize !== undefined && stored?.collapsed[id] === true;
  const openSize = stored?.sizes[id] ?? defaultSize;
  const entry = useRef<PaneEntry>({
    handle: () => panelRef.current,
    collapsedSize,
    lastOpenSize: openSize ?? minSize,
  });

  useEffect(() => {
    entry.current.collapsedSize = collapsedSize;
    register(id, entry.current);
    return () => unregister(id);
  }, [id, collapsedSize, register, unregister]);

  useLayoutEffect(() => {
    if (startsCollapsed) startCollapsed(id);
  }, [id, startsCollapsed, startCollapsed]);

  return (
    <Panel
      id={id}
      panelRef={panelRef}
      defaultSize={startsCollapsed ? collapsedSize : openSize}
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
