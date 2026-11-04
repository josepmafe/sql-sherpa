import { IconBinaryTree2, IconHome, IconTable } from "@tabler/icons-react";
import type { ComponentType } from "react";

// Only screens that exist are listed (Home in ui-4, the Explorer in ui-1, Lineage in ui-2); Ask adds its entry with its screen.
export const NAV_ITEMS: readonly { path: string; label: string; icon: ComponentType<{ size?: number }> }[] = [
  { path: "/", label: "Home", icon: IconHome },
  { path: "/explorer", label: "Explorer", icon: IconTable },
  { path: "/lineage", label: "Lineage", icon: IconBinaryTree2 },
];
