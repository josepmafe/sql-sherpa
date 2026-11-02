import { IconBinaryTree2, IconTable } from "@tabler/icons-react";
import type { ComponentType } from "react";

// Only screens that exist are listed (the Explorer in ui-1, Lineage in ui-2); Home and Ask add theirs with their screens.
export const NAV_ITEMS: readonly { path: string; label: string; icon: ComponentType<{ size?: number }> }[] = [
  { path: "/explorer", label: "Explorer", icon: IconTable },
  { path: "/lineage", label: "Lineage", icon: IconBinaryTree2 },
];
