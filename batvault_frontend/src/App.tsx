// src/App.tsx
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { useEffect } from "react";
import { MantineProvider, createTheme } from "@mantine/core"; // ✅ now complete
import { monitorTokenExpiry } from "./utils/collective/auth";

import LoginPage from "./components/collective/LoginPage";
import OriginsRoute from "./routes/OriginsRoute";
import VaultRoute from "./routes/VaultRoute";
import SuccessScreen from "./components/collective/SuccessScreen";
import ExpiredPage from "./components/collective/ExpiredPage";
import MemoryRoute from "./routes/MemoryRoute";
import { VisitedOriginsProvider } from "./context/VisitedOrigins";

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
              <Routes>
                <Route index element={<OriginsRoute />} />
                <Route path="/origins" element={<OriginsRoute />} />
                <Route path="/memory" element={<MemoryRoute />} />
                <Route path="/collective" element={<LoginPage />} />
                <Route path="/collective/success" element={<SuccessScreen />} />
                <Route path="/collective/vault" element={<VaultRoute />} />
                <Route path="/collective/expired" element={<ExpiredPage />} />
                <Route path="*" element={<p className="text-white p-6">Page not found.</p>} />
              </Routes>
            </Router>
          </div>
        </div>
      </VisitedOriginsProvider>
    </MantineProvider>
  );
}
