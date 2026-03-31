import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Amplify } from "aws-amplify";
import { awsConfig } from "./config";
import "./index.css";
import App from "./App";

Amplify.configure(awsConfig);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
