import AiLoginRedirect, { useAutoLoginParams } from "../AiLoginRedirect";
import { Layout } from "./Layout";

export default function GeneralAssistant() {
  const { userId, userName } = useAutoLoginParams();
  if (userId && userName) {
    return <AiLoginRedirect />;
  }
  return <Layout />;
}
