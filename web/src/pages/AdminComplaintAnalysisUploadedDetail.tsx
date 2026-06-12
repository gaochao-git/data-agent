import React, { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { API_BASE } from '../lib/config';
import { copyToClipboard } from '../lib/utils';

interface Meta {
  id: string;
  created_at: string;
  file_name: string;
  rows_count: number;
  overview_count: number;
  sheet?: string;
  columns_detected?: string[];
}

interface ParsedItem {
  overview?: string;
  decomposition: string;
  in_scope?: string;
  suggestions?: string | string[];
  verification_points?: string;
  checklist?: string | string[];
  law_list?: string | string[];
  notes?: string;
  row_no?: number;  
}

interface GroupView {
  category: string;
  items_count: number;
  items: ParsedItem[];
}

export default function AdminComplaintAnalysisUploadedDetail() {
  const { id } = useParams<{ id: string }>();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [groups, setGroups] = useState<GroupView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    const run = async () => {
      setLoading(true);
      setError(null);
      try {
        const resp = await fetch(`${API_BASE}/api/complaint/complaint-analysis-uploaded/${id}`, {
          credentials: "include",
        });
        if (!resp.ok) throw new Error(`加载失败: ${resp.status}`);
        const data = await resp.json();
        setMeta(data?.meta || null);
        // 新结构：data.parsed 为数组 [{ category: ParsedItem[] }, ...]
        const parsedArr = Array.isArray(data?.parsed) ? data.parsed : [];
        const nextGroups: GroupView[] = [];
        parsedArr.forEach((entry: any) => {
          if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
            Object.entries(entry).forEach(([category, items]) => {
              const list = Array.isArray(items) ? (items as ParsedItem[]) : [];
              nextGroups.push({ category, items_count: list.length, items: list });
            });
          }
        });
        setGroups(nextGroups);
      } catch (e: any) {
        setError(e?.message || '请求失败');
      } finally {
        setLoading(false);
      }
    };
    run();
  }, [id]);

  const headerCols = useMemo(() => meta?.columns_detected || [], [meta]);
  const headerFieldMap: Record<string, string[]> = useMemo(() => ({
    '一级分类': [],
    '一级分类说明': ['first_category_description'],
    '名词辨析': ['glossary'],
    '二级分类说明': ['second_category_description'],
    '二级分类': ['overview'],
    '核实事项': ['decomposition'],
    '是否属于支付部门核实范围': ['in_scope'],
    '处理建议': ['suggestions'],
    '法律法规': ['law_list'],
    '核实要点': ['verification_points'],
    '调阅清单': ['checklist'],
    '注意事项': ['notes']
  }), []);

  const defaultColumnsOrder = useMemo(
    () => ['二级分类', '二级分类说明', '一级分类说明', '名词辨析', '核实事项', '是否属于支付部门核实范围', '处理建议', '法律法规', '核实要点', '调阅清单', '注意事项'],
    []
  );

  const columnsForTable = useMemo(() => {
    const detected = Array.isArray(headerCols) ? headerCols : [];
    const normalizedDetected = detected.map((h) => String(h).trim());
    const hasDetected = normalizedDetected.length > 0;

    if (hasDetected) {
      const filtered = normalizedDetected.filter((h) => h !== '一级分类' && headerFieldMap[h] !== undefined);
      // 如果检测到旧列名“定义”，用“二级分类说明”替换显示名
      const remapped = filtered.map((h) => (h === '定义' ? '二级分类说明' : h));
      // 保持合理顺序：以默认顺序排序，未包含的附在末尾
      const ordered = [...remapped].sort((a, b) => {
        const ia = defaultColumnsOrder.indexOf(a);
        const ib = defaultColumnsOrder.indexOf(b);
        if (ia === -1 && ib === -1) return a.localeCompare(b);
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
      });
      return ordered.length ? ordered : defaultColumnsOrder;
    }
    // 无检测列时使用默认顺序
    return defaultColumnsOrder;
  }, [headerCols, headerFieldMap, defaultColumnsOrder]);

  const formatCST = (s?: string) => {
    if (!s) return '';
    const d = new Date(s);
    if (isNaN(d.getTime())) return s;
    const d2 = new Date(d.getTime() + 8 * 60 * 60 * 1000);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d2.getFullYear()}-${pad(d2.getMonth() + 1)}-${pad(d2.getDate())}T${pad(d2.getHours())}:${pad(d2.getMinutes())}:${pad(d2.getSeconds())}Z`;
  };

  return (
    <div className="fx-page">
      <div className="fx-row" style={{ marginBottom: 12 }}>
        <h2 className="fx-section-title" style={{ margin: 0 }}>投诉分析 - 详情</h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Link className="fx-btn fx-btn--primary" to="/admin/complaint-analysis-uploaded">返回列表</Link>
        </div>
      </div>

      {loading && <div>加载中…</div>}
      {error && <div style={{ color: 'red' }}>{error}</div>}

      {meta && (
        <div className="fx-card fx-card--spacious" style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 16 }}>{meta.file_name}</div>
          <div className="fx-meta">上传时间：{formatCST(meta?.created_at)}</div>
          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <span className="fx-badge">表：{meta.sheet || '-'}</span>
            <span className="fx-badge">行数：{meta.rows_count}</span>
            <span className="fx-badge">概述分组：{meta.overview_count}</span>
          </div>
          <div className="fx-meta" style={{ marginTop: 8 }}>检测到列：{headerCols.join('，') || '-'}</div>
          <div style={{ marginTop: 8 }}>
            <a className="fx-btn fx-btn--primary" href={`${API_BASE}/api/complaint/complaint-analysis-uploaded/${meta.id}/download`} target="_blank" rel="noreferrer">下载原始文件</a>
          </div>
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <div className="fx-card fx-card--spacious">
          {groups.length === 0 && <div>暂无数据</div>}
          {groups.map((g, idx) => (
            <div key={idx} style={{ marginBottom: 16 }}>
              <div className="fx-table-wrap" style={{ marginTop: 8 }}>
                <table className="fx-table fx-table--excel">
                  <thead>
                    <tr>
                      <th className="fx-table__group-th" colSpan={columnsForTable.length}>一级分类：{g.category || '（无分类）'}</th>
                    </tr>
                    <tr>
                      {columnsForTable.map((h) => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {g.items.map((it, i) => (
                      <tr key={i}>
                        {columnsForTable.map((h) => {
                          const keys = headerFieldMap[h] || [h];
                          let v: any = null;
                          for (const k of keys) {
                            if (k && (it as any)[k] != null) {
                              v = (it as any)[k];
                              break;
                            }
                          }
                          let display = '-';
                          if (Array.isArray(v)) display = v.join('；');
                          else if (typeof v === 'string') display = v || '-';
                          else if (v != null) display = String(v);
                          return <td key={h}>{display}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
