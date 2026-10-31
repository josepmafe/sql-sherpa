import {
  IconArrowRight,
  IconCircleCheck,
  IconCircleHalf2,
  IconDatabase,
  IconHash,
  IconHelpCircle,
  IconMathFunction,
  IconPin,
  IconSparkles,
  IconTag,
} from "@tabler/icons-react";
import type { ComponentType } from "react";

import type { components } from "../../api/schema";

type ColumnKind = components["schemas"]["ColumnKind"];
type Resolution = components["schemas"]["Resolution"];

type Icon = ComponentType<{ size?: number; "aria-hidden"?: boolean }>;

// Kind and resolution are an icon plus their name, never a colour alone (WCAG 1.4.1).
const KIND: Record<ColumnKind, { label: string; Icon: Icon }> = {
  passthrough: { label: "passthrough", Icon: IconArrowRight },
  rename: { label: "rename", Icon: IconTag },
  transformed: { label: "transformed", Icon: IconMathFunction },
  row_count: { label: "row count", Icon: IconHash },
  constant: { label: "constant", Icon: IconPin },
  generated: { label: "generated", Icon: IconSparkles },
  source: { label: "source", Icon: IconDatabase },
};
const RESOLUTION: Record<Resolution, { Icon: Icon }> = {
  full: { Icon: IconCircleCheck },
  partial: { Icon: IconCircleHalf2 },
  unresolved: { Icon: IconHelpCircle },
};

const ICON_SIZE = 12;

export function KindLabel({ kind }: { kind: ColumnKind }) {
  const { label, Icon } = KIND[kind];
  return <Labelled Icon={Icon} text={label} />;
}

export function ResolutionLabel({ resolution }: { resolution: Resolution }) {
  return <Labelled Icon={RESOLUTION[resolution].Icon} text={resolution} />;
}

function Labelled({ Icon, text }: { Icon: Icon; text: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 3, whiteSpace: "nowrap" }}>
      <Icon size={ICON_SIZE} aria-hidden />
      {text}
    </span>
  );
}
