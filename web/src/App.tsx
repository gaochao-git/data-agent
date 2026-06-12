import { Routes, Route } from "react-router-dom";
import { useState } from "react";
import { AuthContext } from "@/contexts/authContext";
import AISpecialistHome from "@/pages/AISpecialistHome";
import GeneralAssistant from "@/pages/GeneralAssistant";
import ChatData from "@/pages/ChatData";
import AdminComplaintAnalysisUploaded from "@/pages/AdminComplaintAnalysisUploaded";
import AdminComplaintAnalysisUploadedDetail from "@/pages/AdminComplaintAnalysisUploadedDetail";
import { DashboardEditor, DashboardView } from "@/pages/ChatData/Dashboard";

export default function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(true); // 默认已认证状态

  const logout = () => {
    setIsAuthenticated(false);
  };

  return (
    <AuthContext.Provider
      value={{ isAuthenticated, setIsAuthenticated, logout }}
    >
      <Routes>
        <Route path="/" element={<GeneralAssistant />} />
        <Route path="/specialist" element={<AISpecialistHome />} />
        <Route path="/specialist/c/:conversationId" element={<AISpecialistHome />} />
        <Route path="/general/:conversationId?" element={<GeneralAssistant />} />
        {/* 执法检查 5 个智能体 */}
        <Route path="/fact_analysis/:conversationId?" element={<GeneralAssistant />} />
        <Route path="/report_jcbg/:conversationId?" element={<GeneralAssistant />} />
        <Route path="/report_zgtzs/:conversationId?" element={<GeneralAssistant />} />
        <Route path="/report_zjml/:conversationId?" element={<GeneralAssistant />} />
        <Route path="/report_xxjb/:conversationId?" element={<GeneralAssistant />} />
        <Route path="/complaint/:conversationId?" element={<GeneralAssistant />} />
        <Route path="/expert/:conversationId?" element={<GeneralAssistant />} />
        <Route path="/chatdata/:conversationId?" element={<GeneralAssistant />} />
        {/* 智能问数独立页面 */}
        <Route path="/nl2sql/:conversationId?" element={<ChatData />} />
        {/* 分析大屏路由 */}
        <Route path="/chatdata/dashboard/:dashboardId" element={<DashboardView />} />
        <Route path="/chatdata/dashboard/:dashboardId/edit" element={<DashboardEditor />} />
        <Route path="/admin/complaint-analysis-uploaded" element={<AdminComplaintAnalysisUploaded />} />
        <Route path="/admin/complaint-analysis-uploaded/:id" element={<AdminComplaintAnalysisUploadedDetail />} />
      </Routes>
    </AuthContext.Provider>
  );
}
