import { ActionIcon, Group, TextInput, UnstyledButton } from "@mantine/core";
import { IconPlus, IconX } from "@tabler/icons-react";
import { useState } from "react";

import type { Worksheet } from "./worksheetStore";

type TabsProps = {
  worksheets: Worksheet[];
  activeId: string;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onClose: (id: string) => void;
  onRename: (id: string, name: string) => void;
};

export function WorksheetTabs({ worksheets, activeId, onSelect, onAdd, onClose, onRename }: TabsProps) {
  const [renaming, setRenaming] = useState<string>();

  return (
    <Group gap="xs" wrap="nowrap" role="tablist" aria-label="Worksheets">
      {worksheets.map((worksheet) => {
        const active = worksheet.id === activeId;
        return (
          <Group key={worksheet.id} gap={2} wrap="nowrap">
            {renaming === worksheet.id ? (
              <TextInput
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
                  if (event.key === "Enter") event.currentTarget.blur();
                  if (event.key === "Escape") setRenaming(undefined);
                }}
              />
            ) : (
              <UnstyledButton
                role="tab"
                aria-selected={active}
                fw={active ? 700 : 400}
                fz="sm"
                title="Double-click to rename"
                onClick={() => onSelect(worksheet.id)}
                onDoubleClick={() => setRenaming(worksheet.id)}
              >
                {worksheet.name}
              </UnstyledButton>
            )}
            {worksheets.length > 1 && (
              <ActionIcon
                variant="subtle"
                size="xs"
                aria-label={`Close ${worksheet.name}`}
                onClick={() => onClose(worksheet.id)}
              >
                <IconX size={12} />
              </ActionIcon>
            )}
          </Group>
        );
      })}
      <ActionIcon variant="subtle" size="sm" aria-label="New worksheet" onClick={onAdd}>
        <IconPlus size={14} />
      </ActionIcon>
    </Group>
  );
}
