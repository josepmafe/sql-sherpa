import { Tooltip } from "@mantine/core";
import type { ReactNode } from "react";

export const LINEAGE_DISABLED_HINT = "Lineage arrives with ui-2";

// Disabled elements swallow hover events, so the tooltip sits on a wrapper.
export function DisabledHint({ hint, show, children }: { hint: string; show: boolean; children: ReactNode }) {
  if (!show) return <>{children}</>;
  return (
    <Tooltip label={hint} position="right">
      <div>{children}</div>
    </Tooltip>
  );
}
