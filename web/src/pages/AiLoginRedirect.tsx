import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { API_BASE } from "@/lib/config";

type Status = "idle" | "loading" | "success" | "error";

export function extractUserParams(search: string) {
  const params = new URLSearchParams(search);
  const rawId = params.get("id") ?? params.get("userid") ?? "";
  const rawName = params.get("name") ?? params.get("username") ?? "";
  const redirect = params.get("redirect") ?? "";
  const userId = rawId.trim();
  const userName = rawName.trim();
  return {
    userId,
    userName,
    redirect: redirect.trim(),
  };
}

export function useAutoLoginParams() {
  const location = useLocation();
  return useMemo(
    () => extractUserParams(location.search),
    [location.search],
  );
}

export default function AiLoginRedirect() {
  const { userId, userName, redirect } = useAutoLoginParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!userId || !userName) {
      setStatus("error");
      setErrorMessage("缺少 id 或 name 参数，无法完成登录。");
      return;
    }

    const controller = new AbortController();
    const target = `${API_BASE}/api/inner_login?userid=${encodeURIComponent(
      userId,
    )}&username=${encodeURIComponent(userName)}`;

    const run = async () => {
      setStatus("loading");
      try {
        const response = await fetch(target, {
          method: "GET",
          credentials: "include",
          signal: controller.signal,
        });
        if (!response.ok) {
          const detail = await response.text();
          throw new Error(detail || `登录失败（${response.status}）`);
        }
        setStatus("success");
        if (redirect && redirect.startsWith("/")) {
          navigate(redirect, { replace: true });
        } else {
          navigate({ search: "" }, { replace: true });
        }
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        const message =
          error instanceof Error
            ? error.message
            : "登录失败，请稍后重试。";
        setStatus("error");
        setErrorMessage(message);
      }
    };

    run();

    return () => controller.abort();
  }, [navigate, redirect, userId, userName]);

  if (status === "success") {
    return null;
  }

  return (
    <div className="flex h-screen items-center justify-center bg-gray-50 text-gray-700">
      <div className="rounded-lg bg-white p-6 shadow-md">
        {status === "loading" && (
          <p>正在为 {userName || "用户"} 初始化会话，请稍候…</p>
        )}
        {status === "error" && (
          <div>
            <p className="font-semibold text-red-600">登录失败</p>
            <p className="mt-2 text-sm text-gray-600">{errorMessage}</p>
          </div>
        )}
        {status === "idle" && <p>准备登录…</p>}
      </div>
    </div>
  );
}
