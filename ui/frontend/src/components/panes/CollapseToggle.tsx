import { ActionIcon } from "@mantine/core";
import { IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand } from "@tabler/icons-react";

type Props = {
  collapsed: boolean;
  onToggle: () => void;
  /** The name while the pane is open: what pressing it folds, e.g. "Collapse the table tree". */
  foldLabel: string;
  /** The name while the pane is folded, e.g. "Open the table tree". */
  openLabel: string;
};

export function CollapseToggle({ collapsed, onToggle, foldLabel, openLabel }: Props) {
  return (
    <ActionIcon
      variant="subtle"
      color="gray"
      size="sm"
      aria-expanded={!collapsed}
      aria-label={collapsed ? openLabel : foldLabel}
      onClick={onToggle}
    >
      {collapsed ? <IconLayoutSidebarLeftExpand size={16} /> : <IconLayoutSidebarLeftCollapse size={16} />}
    </ActionIcon>
  );
}
