import { ActionIcon } from "@mantine/core";
import {
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconLayoutSidebarRightCollapse,
  IconLayoutSidebarRightExpand,
} from "@tabler/icons-react";

const ICONS = {
  left: { collapse: IconLayoutSidebarLeftCollapse, expand: IconLayoutSidebarLeftExpand },
  right: { collapse: IconLayoutSidebarRightCollapse, expand: IconLayoutSidebarRightExpand },
} as const;

type Props = {
  collapsed: boolean;
  onToggle: () => void;
  /** The name while the pane is open: what pressing it folds, e.g. "Collapse the table tree". */
  foldLabel: string;
  /** The name while the pane is folded, e.g. "Open the table tree". */
  openLabel: string;
  /** The edge the pane folds to; its icon points that way. */
  side?: "left" | "right";
};

export function CollapseToggle({ collapsed, onToggle, foldLabel, openLabel, side = "left" }: Props) {
  const Icon = ICONS[side][collapsed ? "expand" : "collapse"];
  return (
    <ActionIcon
      variant="subtle"
      color="gray"
      size="sm"
      aria-expanded={!collapsed}
      aria-label={collapsed ? openLabel : foldLabel}
      onClick={onToggle}
    >
      <Icon size={16} />
    </ActionIcon>
  );
}
