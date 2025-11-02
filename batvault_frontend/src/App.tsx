// src/App.tsx
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { useEffect, Suspense, lazy } from "react";
import { MantineProvider, createTheme } from "@mantine/core"; // ✅ now complete
import { monitorTokenExpiry } from "./utils/collective/auth";
import { VisitedOriginsProvider } from "./context/VisitedOrigins";
import { logEvent } from "./utils/logger";
import RouteLoader from "./components/common/RouteLoader";

const OriginsRoute = lazy(() => {
  logEvent("ui.route_chunk_fetch", { route: "origins" });
  return import("./routes/OriginsRoute")
    .then((m) => {
      logEvent("ui.route_chunk_ready", { route: "origins" });
      return { default: m.default };
    })
    .catch((err) => {
      logEvent("ui.route_chunk_error", { route: "origins", error: String(err) });
      throw err;
    });
});

const MemoryRoute = lazy(() => {
  logEvent("ui.route_chunk_fetch", { route: "memory" });
  return import("./routes/MemoryRoute")
    .then((m) => {
      logEvent("ui.route_chunk_ready", { route: "memory" });
      return { default: m.default };
    })
    .catch((err) => {
      logEvent("ui.route_chunk_error", { route: "memory", error: String(err) });
      throw err;
    });
});

const LoginPage = lazy(() => {
  logEvent("ui.route_chunk_fetch", { route: "collective.login" });
  return import("./components/collective/LoginPage")
    .then((m) => {
      logEvent("ui.route_chunk_ready", { route: "collective.login" });
      return { default: m.default };
    })
    .catch((err) => {
      logEvent("ui.route_chunk_error", { route: "collective.login", error: String(err) });
      throw err;
    });
});

const SuccessScreen = lazy(() => {
  logEvent("ui.route_chunk_fetch", { route: "collective.success" });
  return import("./components/collective/SuccessScreen")
    .then((m) => {
      logEvent("ui.route_chunk_ready", { route: "collective.success" });
      return { default: m.default };
    })
    .catch((err) => {
      logEvent("ui.route_chunk_error", { route: "collective.success", error: String(err) });
      throw err;
    });
});

const ExpiredPage = lazy(() => {
  logEvent("ui.route_chunk_fetch", { route: "collective.expired" });
  return import("./components/collective/ExpiredPage")
    .then((m) => {
      logEvent("ui.route_chunk_ready", { route: "collective.expired" });
      return { default: m.default };
    })
    .catch((err) => {
      logEvent("ui.route_chunk_error", { route: "collective.expired", error: String(err) });
      throw err;
    });
});

const VaultRoute = lazy(() => {
  logEvent("ui.route_chunk_fetch", { route: "collective.vault" });
  return import("./routes/VaultRoute")
    .then((m) => {
      logEvent("ui.route_chunk_ready", { route: "collective.vault" });
      return { default: m.default };
    })
    .catch((err) => {
      logEvent("ui.route_chunk_error", { route: "collective.vault", error: String(err) });
      throw err;
    });
});

// ✅ Create theme
const theme = createTheme({
  primaryColor: "blue",
});

export default function App() {
  useEffect(() => {
    monitorTokenExpiry();
  }, []);

  return (
    <MantineProvider theme={theme}>
      <VisitedOriginsProvider>
        <div className="relative min-h-screen bg-black overflow-hidden">
          <div className="relative z-10">
            <Router>
              <Suspense fallback={<RouteLoader />}>
                <Routes>
                  <Route path="/" element={<OriginsRoute />} />
                  <Route path="/memory" element={<MemoryRoute />} />
                  <Route path="/collective" element={<LoginPage />} />
                  <Route path="/collective/success" element={<SuccessScreen />} />
                  <Route path="/collective/vault" element={<VaultRoute />} />
                  <Route path="/collective/expired" element={<ExpiredPage />} />
                  <Route path="*" element={<p className="text-white p-6">Page not found.</p>} />
                </Routes>
              </Suspense>
            </Router>
          </div>
        </div>
      </VisitedOriginsProvider>
    </MantineProvider>
  );
}
