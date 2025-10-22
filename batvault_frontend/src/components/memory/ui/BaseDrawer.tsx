import React from "react";

type Placement = "right" | "bottom";

export default function BaseDrawer({
  open,
  onClose,
  children,
  testId,
  id,
  placement = "right",
  inline = false,
  className,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  testId?: string;
  id?: string;
  placement?: Placement;
  /**
   * When true, renders the drawer **inline** in the page flow instead of a fixed overlay.
   * This lets the whole page remain scrollable and avoids obscuring the short answer.
   */
  inline?: boolean;
  /** Extra classes applied to the outer container (both modes). */
  className?: string;
}) {
  if (!open) return null;
  /**
   * INLINE MODE — becomes part of the normal layout (no overlay/backdrop).
   * We deliberately avoid fixed heights so the **page** scrolls, not the panel.
   */
  if (inline) {
    const inlinePanel =
      placement === "bottom"
        ? "w-full border-t border-white/10"
        : "w-full md:w-[720px] md:ml-auto border-l border-white/10";
    return (
      <section
        data-testid={testId || "drawer"}
        id={id}
        className={[
          "bg-[#0c0f14] shadow-neon-red",
          inlinePanel,
          // no overflow-y here — keep scrolling on the page
          className || "",
        ].join(" ")}
      >
        {children}
      </section>
    );
  }

  /**
   * OVERLAY MODE — existing behavior, kept for other drawers (e.g., Audit).
   */
  const panelClass =
    placement === "bottom"
      ? "left-0 right-0 bottom-0 h-[70vh] w-full border-t border-white/10"
      : "right-0 top-0 h-full w-[720px] max-w-[95vw] border-l border-white/10";
  return (
    <div className="fixed inset-0 z-40" data-testid={testId || "drawer"} id={id}>
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className={"absolute bg-[#0c0f14] overflow-y-auto " + panelClass}>
        {children}
      </div>
    </div>
  );
}