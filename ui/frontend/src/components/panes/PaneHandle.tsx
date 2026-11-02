import { Separator } from "react-resizable-panels";
import classes from "./PaneHandle.module.css";

/** The separator between two panes: a 1 px line in a 6 px hit area. `label` is its accessible name. */
export function PaneHandle({ label }: { label: string }) {
  return <Separator aria-label={label} className={classes.handle} />;
}
