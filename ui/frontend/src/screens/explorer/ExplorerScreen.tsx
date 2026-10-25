import { Box, Group, ScrollArea } from "@mantine/core";
import { useState, type ReactNode } from "react";

import { CatalogTree } from "./CatalogTree";
import { Inspector } from "./Inspector";

type ExplorerProps = {
  // The editor and results fill the centre and supply the menu handlers.
  children?: ReactNode;
  onPreview?: (qualifiedName: string) => void;
  onInsertSelect?: (qualifiedName: string) => void;
};

const SIDE_WIDTH = 300;
const SIDE_STYLE = { flexShrink: 0, width: SIDE_WIDTH } as const;

export function ExplorerScreen({ children, onPreview, onInsertSelect }: ExplorerProps) {
  const [selected, setSelected] = useState<string>();

  return (
    <Group align="stretch" wrap="nowrap" gap="md" h="calc(100vh - 100px)">
      <ScrollArea style={SIDE_STYLE}>
        <CatalogTree selected={selected} onSelect={setSelected} onPreview={onPreview} onInsertSelect={onInsertSelect} />
      </ScrollArea>
      <Box style={{ flex: 1, minWidth: 0 }}>{children}</Box>
      <ScrollArea style={SIDE_STYLE}>
        <Inspector table={selected} />
      </ScrollArea>
    </Group>
  );
}
