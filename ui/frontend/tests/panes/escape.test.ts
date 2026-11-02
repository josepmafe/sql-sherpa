import { afterEach, describe, expect, it } from "vitest";

import { isFreeEscape } from "../../src/components/panes";

function escapeOn(target: Element): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  Object.defineProperty(event, "target", { value: target });
  return event;
}

function editor(withTooltip: boolean): HTMLElement {
  const root = document.createElement("div");
  root.innerHTML = `<div class="cm-editor"><div class="cm-content"></div>${
    withTooltip ? '<div class="cm-tooltip cm-tooltip-autocomplete"></div>' : ""
  }</div>`;
  document.body.append(root);
  return root.querySelector(".cm-content") as HTMLElement;
}

describe("isFreeEscape", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("is free on a plain target", () => {
    expect(isFreeEscape(escapeOn(document.body))).toBe(true);
  });

  it("is not free once a widget took it", () => {
    const event = escapeOn(document.body);
    event.preventDefault();

    expect(isFreeEscape(event)).toBe(false);
  });

  it("is not free in a code editor whose completion list is open", () => {
    expect(isFreeEscape(escapeOn(editor(true)))).toBe(false);
  });

  it("is free in a code editor with no completion list open", () => {
    expect(isFreeEscape(escapeOn(editor(false)))).toBe(true);
  });

  it("is not free inside a dialog, whose own Escape closes it", () => {
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.innerHTML = "<button>x</button>";
    document.body.append(dialog);

    expect(isFreeEscape(escapeOn(dialog.querySelector("button") as HTMLElement))).toBe(false);
  });
});
