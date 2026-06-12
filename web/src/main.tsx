import "./polyfills/objectHasOwn";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from 'sonner';
import App from "./App.tsx";
import "./index.css";

// 让 Router 的基路径与 Vite 的 base 保持一致（去除多余的结尾斜杠）
const rawBaseUrl = import.meta.env.BASE_URL || '/';
const basename = rawBaseUrl === '/' ? '/' : rawBaseUrl.replace(/\/+$/, '');

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter
      basename={basename}
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true
      }}
    >
      <App />
      <Toaster position="top-center" />
    </BrowserRouter>
  </StrictMode>
);
