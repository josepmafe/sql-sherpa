// Menus, completion lists, tooltips and dialogs close on their own Escape.
const POPUP_SELECTOR = '[role="menu"], [role="listbox"], [role="dialog"], .cm-tooltip';
// The focus stays in the editor while its completion list is open.
const EDITOR_WITH_POPUP_SELECTOR = ".cm-editor:has(.cm-tooltip)";

/**
 * An Escape that is for a layer of the page: no widget took it and it is not inside a popup. The innermost layer
 * (a peek) listens in the capture phase and calls `preventDefault`; the layers under it listen in the bubble phase
 * and skip an Escape that is no longer free, so one key press closes one layer.
 */
export function isFreeEscape(event: KeyboardEvent): boolean {
  const inPopup =
    event.target instanceof Element &&
    (event.target.closest(POPUP_SELECTOR) !== null || event.target.closest(EDITOR_WITH_POPUP_SELECTOR) !== null);
  return event.key === "Escape" && !event.defaultPrevented && !inPopup;
}
