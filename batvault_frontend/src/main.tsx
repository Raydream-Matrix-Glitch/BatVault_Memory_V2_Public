import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import "./styles/memory-theme.css";
import { loadRuntimeConfig } from "./config/runtime";


// Bootstrap: fetch and validate runtime config before mounting the app.
const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Missing root element in index.html");
}

(async function bootstrap() {
  await loadRuntimeConfig();
  createRoot(rootElement).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
})().catch((err) => {
  // Fail-closed with explicit error; no broad catch.
  const msg = (err instanceof Error) ? err.message : String(err);
  document.body.innerHTML = `<pre style="color:#b00;padding:1rem;">${msg}</pre>`;
});
