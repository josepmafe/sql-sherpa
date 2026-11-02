import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// UI spec §6.1: a strip opens over the content after the pointer rests on it, or at once on focus.
export const PEEK_DELAY_MS = 200;
export const PEEK_LEAVE_MS = 300;

export type PeekProps = {
  onPointerEnter: () => void;
  onPointerLeave: () => void;
  onFocus: () => void;
  onBlur: (event: React.FocusEvent) => void;
};

/** Whether a collapsed strip is open over the content. Spread `peekProps` on the element that holds the strip and its overlay. */
export function usePeek(collapsed: boolean): {
  peeking: boolean;
  peekProps: PeekProps;
} {
  const [open, setOpen] = useState(false);
  const pointerIn = useRef(false);
  const focusIn = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const clear = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = undefined;
  }, []);
  const settle = useCallback(() => {
    clear();
    if (!pointerIn.current && !focusIn.current) timer.current = setTimeout(() => setOpen(false), PEEK_LEAVE_MS);
  }, [clear]);

  const peekProps = useMemo<PeekProps>(
    () => ({
      onPointerEnter: () => {
        pointerIn.current = true;
        clear();
        timer.current = setTimeout(() => setOpen(true), PEEK_DELAY_MS);
      },
      onPointerLeave: () => {
        pointerIn.current = false;
        settle();
      },
      onFocus: () => {
        focusIn.current = true;
        clear();
        setOpen(true);
      },
      onBlur: (event) => {
        // Focus moving between two elements inside the strip is not leaving it.
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        focusIn.current = false;
        settle();
      },
    }),
    [clear, settle],
  );

  useEffect(() => clear, [clear]);
  useEffect(() => {
    if (!collapsed) {
      clear();
      pointerIn.current = false;
      focusIn.current = false;
    }
  }, [collapsed, clear]);

  // Expanding ends a peek (state adjusted while rendering, not in an effect).
  const [wasCollapsed, setWasCollapsed] = useState(collapsed);
  if (wasCollapsed !== collapsed) {
    setWasCollapsed(collapsed);
    if (!collapsed) setOpen(false);
  }

  return { peeking: collapsed && open, peekProps };
}
