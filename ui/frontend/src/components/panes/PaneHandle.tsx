import { Separator } from "react-resizable-panels";
import classes from "./PaneHandle.module.css";
import { usePaneGroup } from "./PaneGroup";

/** The separator between two panes: a 1 px line in a 6 px hit area. `label` is its accessible name. */
export function PaneHandle({ label }: { label: string }) {
  const { persistSoon } = usePaneGroup();
  // The library resets the sizes on a double-click but does not report it as the user's change.
  return <Separator aria-label={label} className={classes.handle} onDoubleClick={persistSoon} />;
}
