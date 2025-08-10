import { createContext, useCallback, useContext, useState } from "react";

type Ctx = { hasVisited: boolean; markVisited: () => void };

const VisitedOriginsCtx = createContext<Ctx | undefined>(undefined);

export const VisitedOriginsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [hasVisited, setHasVisited] = useState(
    () => localStorage.getItem("bv:visited-origins") === "1",
  );

  const markVisited = useCallback(() => {
    if (!hasVisited) {
      localStorage.setItem("bv:visited-origins", "1");
      setHasVisited(true);
    }
  }, [hasVisited]);

  return (
    <VisitedOriginsCtx.Provider value={{ hasVisited, markVisited }}>
      {children}
    </VisitedOriginsCtx.Provider>
  );
};

export const useVisitedOrigins = () => {
  const ctx = useContext(VisitedOriginsCtx);
  if (!ctx) throw new Error("useVisitedOrigins must be used inside <VisitedOriginsProvider>");
  return ctx;
};