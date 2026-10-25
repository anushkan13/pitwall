import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./index.css";

// Pit Wall has no service worker. Remove any a browser still has, so it can never serve an old build.
navigator.serviceWorker?.getRegistrations().then((regs) => regs.forEach((r) => r.unregister())).catch(() => {});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
