import { Box, ScrollArea, Stack } from "@mantine/core";
import { IconListTree } from "@tabler/icons-react";
import { type ComponentProps, useEffect, useLayoutEffect, useRef, useState } from "react";

import { CollapseToggle, isFreeEscape, usePaneCollapse, usePeek } from "../../components/panes";
import { CatalogTree } from "./CatalogTree";

export const TREE_PANE = "tree";
export const TREE_STRIP_WIDTH = 40;
// What the tree opens to while it is peeked at; the pane's own open size is the user's.
const PEEK_WIDTH = 320;
const PEEK_Z_INDEX = 20;
const ICON_SIZE = 18;
const FOLD_LABEL = "Collapse the table tree";
const OPEN_LABEL = "Open the table tree";

type TreeProps = ComponentProps<typeof CatalogTree>;

type Props = Omit<TreeProps, "headerAction">;

/** The tree as a pane. Folded, it is a strip with its icon; the strip opens the tree over the editor on hover or focus (UI spec §6.1). */
export function TreePane({ onSelect, ...tree }: Props) {
  const { collapsed, toggle, toggleRef } = usePaneCollapse(TREE_PANE);
  const { peeking, peekProps } = usePeek(collapsed);
  const [dismissed, setDismissed] = useState(false);
  const open = peeking && !dismissed;
  const strip = useRef<HTMLDivElement>(null);
  const [anchor, setAnchor] = useState<DOMRect>();
  useLayoutEffect(() => {
    if (!open) return;
    const measure = () => setAnchor(strip.current?.getBoundingClientRect());
    measure();
    // The strip moves with the window (a resize changes the layout above it).
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open]);

  // A dismissed peek stays dismissed until the pointer and the focus have left, so the next peek can open.
  const [wasPeeking, setWasPeeking] = useState(peeking);
  if (wasPeeking !== peeking) {
    setWasPeeking(peeking);
    if (!peeking) setDismissed(false);
  }

  useEffect(() => {
    if (!open) return;
    // Innermost layer: it takes the Escape before the layers under it see it.
    const close = (event: KeyboardEvent) => {
      if (!isFreeEscape(event)) return;
      event.preventDefault();
      setDismissed(true);
    };
    document.addEventListener("keydown", close, true);
    return () => document.removeEventListener("keydown", close, true);
  }, [open]);

  if (!collapsed) {
    return (
      <ScrollArea h="100%" offsetScrollbars="y">
        <CatalogTree
          {...tree}
          onSelect={onSelect}
          headerAction={
            <CollapseToggle
              collapsed={false}
              onToggle={toggle}
              foldLabel={FOLD_LABEL}
              openLabel={OPEN_LABEL}
              buttonRef={toggleRef}
            />
          }
        />
      </ScrollArea>
    );
  }

  return (
    <Stack ref={strip} align="center" gap="xs" pt="xs" h="100%" {...peekProps}>
      <CollapseToggle
        collapsed
        onToggle={toggle}
        foldLabel={FOLD_LABEL}
        openLabel={OPEN_LABEL}
        buttonRef={toggleRef}
      />
      <IconListTree size={ICON_SIZE} aria-hidden />
      {/* Fixed, so the pane's clipping does not cut it; it stays here in the DOM so the Tab key goes from the strip's
          button into the tree. */}
      {open && (
        <Box
          role="region"
          aria-label="Table tree"
          bg="var(--mantine-color-body)"
          style={{
            position: "fixed",
            top: anchor?.top,
            height: anchor?.height,
            left: anchor?.right,
            width: PEEK_WIDTH,
            zIndex: PEEK_Z_INDEX,
            borderRight: "1px solid var(--mantine-color-default-border)",
            boxShadow: "var(--mantine-shadow-md)",
          }}
        >
          <ScrollArea h="100%" offsetScrollbars="y" p="xs">
            <CatalogTree
              {...tree}
              onSelect={(table) => {
                onSelect(table);
                // A click ends the peek: the focus leaves the row that is about to disappear.
                if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
                setDismissed(true);
              }}
            />
          </ScrollArea>
        </Box>
      )}
    </Stack>
  );
}
