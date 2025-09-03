import "./traceGlobals";            // ⬅  add this line
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import "./styles/memory-theme.css";


const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Missing root element in index.html");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
