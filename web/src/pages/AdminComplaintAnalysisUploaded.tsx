import React, { useEffect, useMemo, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { API_BASE } from '../lib/config';

interface UploadItem {
  id: string;
  created_at: string;
  file_name: string;
  rows_count: number;
  overview_count: number;
}

export default function AdminComplaintAnalysisUploaded() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const fetchList = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${API_BASE}/api/complaint/complaint-analysis-uploaded`, {
        credentials: 'include',
      });
      if (!resp.ok) throw new Error(`加载失败: ${resp.status}`);
      const data = await resp.json();
      setItems(data || []);
    } catch (e: any) {
      setError(e?.message || '请求失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
  }, []);

  const onUpload = async () => {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${API_BASE}/api/complaint/complaint-analysis-uploaded`, {
        method: 'POST',
        body: form,
        credentials: 'include',
      });
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `上传失败: ${resp.status}`);
      }
      await fetchList();
      setFile(null);
    } catch (e: any) {
      setError(e?.message || '上传失败');
    } finally {
      setLoading(false);
    }
  };

  const sorted = useMemo(() => {
    return [...items].sort((a, b) => {
      const ta = a.created_at || '';
      const tb = b.created_at || '';
      return tb.localeCompare(ta);
    });
  }, [items]);

  return (
    <div className="fx-page">
      <h2 className="fx-section-title">投诉分析 - 已上传Excel</h2>
      <div className="fx-card fx-card--spacious" style={{ marginTop: 12, marginBottom: 16 }}>
        <div className="fx-row" style={{ alignItems: 'center' }}>
          <div>
            <div className="fx-section-title" style={{ margin: 0 }}>上传Excel文件</div>
            <div className="fx-meta">支持 .xlsx / .xls</div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              style={{ display: 'none' }}
            />
            <button className="fx-btn" onClick={() => fileInputRef.current?.click()}>选择文件</button>
            <span className="fx-badge">{file ? file.name : '未选择任何文件'}</span>
            <button className="fx-btn fx-btn--primary" onClick={onUpload} disabled={!file || loading}>
              {loading ? '上传中…' : '上传Excel'}
            </button>
          </div>
        </div>
        {error && <div style={{ color: 'red', marginTop: 8 }}>{error}</div>}
      </div>

      <div className="fx-card fx-card--spacious">
        <div className="fx-table-wrap">
          <table className="fx-table fx-table--zebra">
            <thead>
              <tr>
                <th>文件名称</th>
                <th>行数</th>
                <th>概述分组</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && sorted.length === 0 ? (
                <tr>
                  <td colSpan={4}>加载中…</td>
                </tr>
              ) : !loading && sorted.length === 0 ? (
                <tr>
                  <td colSpan={4}>暂无数据</td>
                </tr>
              ) : (
                sorted.map((it) => (
                  <tr key={it.id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{it.file_name}</div>
                      <div className="fx-meta">上传时间：{formatCST(it.created_at)}</div>
                    </td>
                    <td>{it.rows_count}</td>
                    <td>{it.overview_count}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                        <button className="fx-btn fx-btn--primary" onClick={() => navigate(`/admin/complaint-analysis-uploaded/${it.id}`)}>查看详情</button>
                        <a className="fx-btn" href={`${API_BASE}/api/complaint/complaint-analysis-uploaded/${it.id}/download`} target="_blank" rel="noreferrer">下载原始文件</a>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}


const formatCST = (s?: string) => {
  if (!s) return '';
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  const d2 = new Date(d.getTime() + 8 * 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d2.getFullYear()}-${pad(d2.getMonth() + 1)}-${pad(d2.getDate())}T${pad(d2.getHours())}:${pad(d2.getMinutes())}:${pad(d2.getSeconds())}Z`;
};
