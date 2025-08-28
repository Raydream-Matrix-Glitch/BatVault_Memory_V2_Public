// src/routes/VaultRoute.tsx
import { Navigate } from "react-router-dom";
import { isTokenValid } from "../utils/collective/auth";
import VaultPage from "../components/collective/VaultPage";

export default function VaultRoute() {
  const token = localStorage.getItem("access_token");

  if (!isTokenValid(token)) {
    localStorage.removeItem("access_token");
    return <Navigate to="/expired" replace />;
  }

  return <VaultPage />;
}
