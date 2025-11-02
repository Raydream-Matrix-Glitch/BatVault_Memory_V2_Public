// src/components/common/RouteLoader.tsx
import { useEffect } from "react";
import { logEvent } from "../../utils/logger";

export default function RouteLoader() {
  useEffect(() => {
    logEvent("ui.route_loading_fallback", {
      ts: Date.now(),
      nav: typeof window !== "undefined" ? window.location?.pathname : undefined,
    });
  }, []);

  return (
    <div className="w-full flex items-center justify-center py-12 text-sm text-neutral-300">
      Loading…
    </div>
  );
}