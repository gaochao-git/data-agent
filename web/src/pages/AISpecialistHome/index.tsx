import AiLoginRedirect, { useAutoLoginParams } from "../AiLoginRedirect";
import AINewHomePage from "./AINewHome";

// 导出内部组件供嵌入式布局使用
export { default as AINewHomeContent } from "./AINewHome";

export default function AISpecialistHome() {
  const { userId, userName } = useAutoLoginParams();
  const hasAutoLoginParams = Boolean(userId && userName);

  if (hasAutoLoginParams) {
    return <AiLoginRedirect />;
  }

  return <AINewHomePage />;
}
