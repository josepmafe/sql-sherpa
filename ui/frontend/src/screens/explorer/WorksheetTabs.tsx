import { ActionIcon, Box, Group, Tabs, TextInput } from "@mantine/core";
import { IconPlus, IconX } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import type { Worksheet } from "./worksheetStore";

type TabsProps = {
  worksheets: Worksheet[];
  activeId: string;
  onAdd: () => void;
  onClose: (id: string) => void;
  onRename: (id: string, name: string) => void;
};

const RENAME_HINT = "Double-click or press F2 to rename";
const CLOSE_HINT = "Delete closes it";
// After a close, the focus goes to whichever tab became active.
const ACTIVE_TAB = Symbol("active tab");
const CLOSE_MARK_STYLE = { display: "inline-flex", cursor: "pointer" } as const;

// The tab list of the Explorer's <Tabs>, which selects a worksheet, moves between tabs with the arrow keys and ties
// each tab to its panel. Only tabs sit in the list, so the new-worksheet button is next to it.
export function WorksheetTabs({ worksheets, activeId, onAdd, onClose, onRename }: TabsProps) {
  const [renaming, setRenaming] = useState<string>();
  const tabs = useRef(new Map<string, HTMLButtonElement>());
  // A keyboard rename or close removes the focused element; the next render gives the focus to this tab.
  const refocus = useRef<string | typeof ACTIVE_TAB>(undefined);
  useEffect(() => {
    const target = refocus.current === ACTIVE_TAB ? activeId : refocus.current;
    refocus.current = undefined;
    if (target !== undefined) tabs.current.get(target)?.focus();
  });
  const closable = worksheets.length > 1;
  const close = (id: string) => {
    refocus.current = ACTIVE_TAB;
    onClose(id);
  };

  return (
    <Group gap="xs" wrap="nowrap">
      <Tabs.List>
        {worksheets.map((worksheet) =>
          renaming === worksheet.id ? (
            <TextInput
              key={worksheet.id}
              size="xs"
              aria-label="Worksheet name"
              defaultValue={worksheet.name}
              // The field appears because the user asked to rename the tab, so focus goes where they expect it.
              // eslint-disable-next-line jsx-a11y/no-autofocus
              autoFocus
              onFocus={(event) => event.currentTarget.select()}
              onBlur={(event) => {
                const name = event.currentTarget.value.trim();
                if (name !== "") onRename(worksheet.id, name);
                setRenaming(undefined);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  refocus.current = worksheet.id;
                  event.currentTarget.blur();
                }
                if (event.key === "Escape") {
                  refocus.current = worksheet.id;
                  setRenaming(undefined);
                }
              }}
            />
          ) : (
            <Tabs.Tab
              key={worksheet.id}
              value={worksheet.id}
              ref={(element: HTMLButtonElement | null) => {
                if (element) tabs.current.set(worksheet.id, element);
                else tabs.current.delete(worksheet.id);
              }}
              title={closable ? `${RENAME_HINT}; ${CLOSE_HINT}` : RENAME_HINT}
              onDoubleClick={() => setRenaming(worksheet.id)}
              onKeyDown={(event) => {
                if (event.key === "F2") setRenaming(worksheet.id);
                if (event.key === "Delete" && closable) close(worksheet.id);
              }}
              rightSection={
                closable && (
                  // A mouse shortcut for Delete, hidden from assistive technology: a tab may not hold a button.
                  <Box
                    component="span"
                    aria-hidden
                    title={`Close ${worksheet.name}`}
                    style={CLOSE_MARK_STYLE}
                    onClick={(event) => {
                      event.stopPropagation();
                      close(worksheet.id);
                    }}
                  >
                    <IconX size={12} />
                  </Box>
                )
              }
            >
              {worksheet.name}
            </Tabs.Tab>
          ),
        )}
      </Tabs.List>
      <ActionIcon variant="subtle" size="sm" aria-label="New worksheet" onClick={onAdd}>
        <IconPlus size={14} />
      </ActionIcon>
    </Group>
  );
}
