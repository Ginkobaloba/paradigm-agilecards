import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { routerBasename } from "./lib/baseUrl";
import { APP_BRAND } from "./lib/brand";
import { claimPortalToken } from "./lib/portalHandoff";
import "./styles/globals.css";

// Reflect the deployment brand in the document title (the static
// index.html title is the standalone default; this overrides at runtime).
document.title = APP_BRAND;

// Claim a portal-issued JWT from the URL fragment before React mounts, so
// the auth state initializer sees the token and skips the manual gate.
claimPortalToken();

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root not found");

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <BrowserRouter basename={routerBasename}>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
