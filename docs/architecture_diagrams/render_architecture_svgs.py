from pathlib import Path

OUT_DIR = Path('docs/architecture_diagrams/out')
OUT_DIR.mkdir(parents=True, exist_ok=True)
FONT = 'PingFang SC, Microsoft YaHei, Arial, sans-serif'


def svg_header(width: int, height: int) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>
    .title {{ font: 700 28px {FONT}; fill: #152033; }}
    .subtitle {{ font: 600 18px {FONT}; fill: #355070; }}
    .label {{ font: 500 16px {FONT}; fill: #203040; }}
    .small {{ font: 400 13px {FONT}; fill: #526072; }}
    .box {{ fill: #ffffff; stroke: #355070; stroke-width: 2; rx: 16; ry: 16; }}
    .zone-dmz {{ fill: #f7fbff; stroke: #5b8def; stroke-width: 2.5; rx: 22; ry: 22; }}
    .zone-app {{ fill: #f5fcf8; stroke: #3aa675; stroke-width: 2.5; rx: 22; ry: 22; }}
    .zone-local {{ fill: #fffaf0; stroke: #e6a23c; stroke-width: 2.5; rx: 22; ry: 22; }}
    .zone-uat {{ fill: #f7fbff; stroke: #5b8def; stroke-width: 2.5; rx: 22; ry: 22; }}
    .zone-prod {{ fill: #f5fcf8; stroke: #3aa675; stroke-width: 2.5; rx: 22; ry: 22; }}
    .arrow {{ stroke: #4a5568; stroke-width: 2.5; fill: none; marker-end: url(#arrow); }}
    .arrow2 {{ stroke: #4a5568; stroke-width: 2; fill: none; marker-end: url(#arrow); }}
    .dash {{ stroke-dasharray: 8 6; }}
    .note {{ font: 500 14px {FONT}; fill: #1f3a5f; }}
</style>
<defs>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
        <path d="M0,0 L0,6 L9,3 z" fill="#4a5568" />
    </marker>
</defs>
'''


def rect(x, y, w, h, cls, title, lines=None):
    lines = lines or []
    parts = [f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="16" ry="16" />']
    parts.append(f'<text class="label" x="{x + w/2}" y="{y + 30}" text-anchor="middle">{title}</text>')
    for i, line in enumerate(lines):
        parts.append(f'<text class="small" x="{x + w/2}" y="{y + 56 + i*18}" text-anchor="middle">{line}</text>')
    return '\n'.join(parts)


def db(x, y, w, h, title, lines=None):
    lines = lines or []
    rx = w / 2
    parts = [
        f'<ellipse cx="{x + w/2}" cy="{y + 18}" rx="{rx}" ry="18" fill="#fff5f5" stroke="#d9485f" stroke-width="2" />',
        f'<rect x="{x}" y="{y + 18}" width="{w}" height="{h - 36}" fill="#fff5f5" stroke="#d9485f" stroke-width="2" />',
        f'<ellipse cx="{x + w/2}" cy="{y + h - 18}" rx="{rx}" ry="18" fill="#fff5f5" stroke="#d9485f" stroke-width="2" />',
        f'<text class="label" x="{x + w/2}" y="{y + h/2 - 4}" text-anchor="middle">{title}</text>'
    ]
    for i, line in enumerate(lines):
        parts.append(f'<text class="small" x="{x + w/2}" y="{y + h/2 + 18 + i*16}" text-anchor="middle">{line}</text>')
    return '\n'.join(parts)


def arrow(x1, y1, x2, y2, label=None, cls='arrow'):
    midx = (x1 + x2) / 2
    midy = (y1 + y2) / 2
    parts = [f'<line class="{cls}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" />']
    if label:
        parts.append(f'<text class="note" x="{midx}" y="{midy - 8}" text-anchor="middle">{label}</text>')
    return '\n'.join(parts)


def system_architecture():
    width, height = 1520, 860
    parts = [svg_header(width, height)]
    parts.append('<rect x="40" y="30" width="1440" height="790" fill="#f8fafc" rx="28" ry="28" />')
    parts.append('<text class="title" x="60" y="75">系统架构图</text>')

    parts.append('<rect class="zone-dmz" x="260" y="120" width="520" height="600" />')
    parts.append('<text class="subtitle" x="290" y="160">DMZ区</text>')
    parts.append('<rect class="zone-app" x="840" y="120" width="560" height="600" />')
    parts.append('<text class="subtitle" x="870" y="160">App区</text>')

    parts.append(rect(70, 340, 130, 90, 'box', '用户浏览器'))
    parts.append(rect(330, 250, 220, 150, 'box', 'DMZ nginx', ['统一访问入口', '按 location 分流']))
    parts.append(rect(590, 300, 150, 110, 'box', 'Java Web 接入层', ['统一 API', '/agents/ 转发入口']))

    parts.append(rect(920, 250, 180, 110, 'box', 'Python Agent服务', ['fxcjg/server']))
    parts.append(rect(920, 470, 180, 110, 'box', 'Java Service', ['用户/权限/基础业务']))
    parts.append(db(1180, 340, 130, 180, '共享数据库(DB)'))

    parts.append(arrow(200, 385, 330, 325, 'HTTP/HTTPS'))
    parts.append(arrow(550, 325, 590, 335, 'API 请求'))
    parts.append(arrow(740, 345, 920, 305, '/agents/* HTTP 转发'))
    parts.append(arrow(740, 385, 920, 525, 'Dubbo'))
    parts.append(arrow(1100, 305, 1180, 390, '读写'))
    parts.append(arrow(1100, 525, 1180, 470, '读写'))

    parts.append('<text class="small" x="335" y="430">静态页面资源(psoss-fe)由 DMZ nginx 指定目录直接承载</text>')
    parts.append('<text class="small" x="60" y="785">说明：浏览器统一访问 DMZ nginx；nginx 按 location 分流到静态页面资源或 Java Web。Java Web 再分别调用 Python Agent 服务与 Java Service，二者共用一个数据库。</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def request_flow():
    width, height = 1580, 980
    xs = [120, 340, 570, 800, 1030, 1260, 1460]
    labels = ['浏览器', 'psoss-fe', 'DMZ Java Web', 'App nginx', 'Python Agent服务', 'Java Service', '数据库']
    parts = [svg_header(width, height)]
    parts.append('<rect x="40" y="30" width="1500" height="920" fill="#f8fafc" rx="28" ry="28" />')
    parts.append('<text class="title" x="60" y="75">调用链路图</text>')
    parts.append('<rect x="70" y="110" width="1440" height="360" fill="#f7fbff" stroke="#5b8def" stroke-width="2" rx="20" ry="20" />')
    parts.append('<text class="subtitle" x="100" y="145">普通业务接口</text>')
    parts.append('<rect x="70" y="500" width="1440" height="390" fill="#f5fcf8" stroke="#3aa675" stroke-width="2" rx="20" ry="20" />')
    parts.append('<text class="subtitle" x="100" y="535">/agents/ 接口</text>')
    for x, label in zip(xs, labels):
        parts.append(f'<text class="label" x="{x}" y="185" text-anchor="middle">{label}</text>')
        parts.append(f'<line x1="{x}" y1="200" x2="{x}" y2="430" stroke="#cbd5e0" stroke-width="2" stroke-dasharray="6 6" />')
        parts.append(f'<text class="label" x="{x}" y="575" text-anchor="middle">{label}</text>')
        parts.append(f'<line x1="{x}" y1="590" x2="{x}" y2="850" stroke="#cbd5e0" stroke-width="2" stroke-dasharray="6 6" />')
    for x1, x2, y, lab in [
        (120, 340, 245, '打开页面/触发请求'),
        (340, 570, 285, '调用普通接口'),
        (570, 1260, 325, 'Dubbo 调用'),
        (1260, 1460, 365, '查询/写入数据'),
        (1460, 340, 405, '返回结果并展示')
    ]:
        parts.append(arrow(x1, y, x2, y, lab, 'arrow2'))
    for x1, x2, y, lab in [
        (120, 340, 635, '发起 Agents 请求'),
        (340, 570, 675, '调用 /agents/*'),
        (570, 800, 715, 'HTTP 转发'),
        (800, 1030, 755, '负载到 Python 实例'),
        (1030, 1460, 795, '查询/写入 Agents 数据'),
        (1460, 340, 835, '返回结果并展示')
    ]:
        parts.append(arrow(x1, y, x2, y, lab, 'arrow2'))
    parts.append('</svg>')
    return '\n'.join(parts)


def deployment_view():
    width, height = 1460, 820
    parts = [svg_header(width, height)]
    parts.append('<rect x="40" y="30" width="1380" height="750" fill="#f8fafc" rx="28" ry="28" />')
    parts.append('<text class="title" x="60" y="75">部署图</text>')

    parts.append('<rect class="zone-local" x="80" y="120" width="620" height="560" />')
    parts.append('<text class="subtitle" x="110" y="160">本地开发</text>')
    parts.append(rect(130, 240, 150, 90, 'box', 'psoss-fe', ['npm run dev']))
    parts.append(rect(330, 235, 180, 100, 'box', 'Java Web 接入层'))
    parts.append(rect(560, 235, 120, 100, 'box', 'Java Service'))
    parts.append(arrow(510, 285, 560, 285, 'Dubbo'))
    parts.append('<text class="small" x="130" y="390">本地开发维护 3 个应用：psoss-fe、Java Web 接入层、Java Service。</text>')
    parts.append('<text class="small" x="130" y="420">本地 Java Web 的 /agents 请求不会在本地处理，而是转发到 UAT 的 Python Agent。</text>')

    parts.append('<rect class="zone-uat" x="760" y="120" width="620" height="560" />')
    parts.append('<text class="subtitle" x="790" y="160">UAT</text>')
    parts.append(rect(820, 235, 180, 100, 'box', 'Java Web'))
    parts.append(rect(1060, 235, 140, 100, 'box', 'Java Service'))
    parts.append(rect(940, 430, 180, 110, 'box', 'Python Agent', ['单节点']))
    parts.append(arrow(1000, 285, 1060, 285, 'Dubbo'))
    parts.append('<text class="small" x="820" y="380">UAT 自身也有完整链路：Java Web、Java Service、Python Agent。</text>')
    parts.append('<text class="small" x="820" y="405">但当前开发联调只复用其中的 Python Agent 服务。</text>')

    parts.append(arrow(510, 320, 940, 465, '/agents/ 转发验证', 'arrow dash'))
    parts.append('<text class="small" x="80" y="735">说明：此图只描述“本地开发 + UAT 验证”关系。UAT 存在自己的完整应用链路，但当前复用点仅为 UAT 的 Python Agent。</text>')
    parts.append('</svg>')
    return "\n".join(parts)


(OUT_DIR / 'system_architecture.svg').write_text(system_architecture(), encoding='utf-8')
(OUT_DIR / 'request_flow.svg').write_text(request_flow(), encoding='utf-8')
(OUT_DIR / 'deployment_view.svg').write_text(deployment_view(), encoding='utf-8')
print('generated:', *(str(p) for p in sorted(OUT_DIR.glob('*.svg'))), sep='\n')
