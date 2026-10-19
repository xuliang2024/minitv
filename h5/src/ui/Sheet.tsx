import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconClose } from "../icons";

/**
 * Bottom sheet primitives shared by the Me page. The mask/sheet sit above the
 * tab bar (z 11/12) on purpose: the tab bar is a flex child with z-index 10 and
 * would otherwise paint over an open sheet.
 */
export function Sheet({
  title,
  onClose,
  children,
  className = "",
  testId,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  const content = (
    <>
      <button className="sheet-mask me-sheet-mask" onClick={onClose} aria-label={title} />
      <div
        className={`sheet me-sheet ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
      >
        <div className="sheet-handle" />
        <header className="me-sheet-head">
          <h3>{title}</h3>
          <button type="button" className="close-btn" onClick={onClose} aria-label={title}>
            <IconClose size={16} />
          </button>
        </header>
        <div className="me-sheet-body">{children}</div>
      </div>
    </>
  );

  // The Me page scrolls inside .stage; keeping the sheet in that scroll box
  // would let it drift off-screen. Portal it next to .stage, like the login sheet.
  const host = typeof document === "undefined" ? null : document.getElementById("app");
  return host ? createPortal(content, host) : content;
}
