import os
import json
import sqlite3
import datetime
from pathlib import Path

# CDN으로 사용할 Vis.js 라이브러리 주소
VIS_JS_CDN = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cortex Architecture Map</title>
    <script type="text/javascript" src="{vis_js_cdn}"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style type="text/css">
        :root {{
            --bg-base: #0f172a;
            --bg-panel: rgba(30, 41, 59, 0.7);
            --border-panel: rgba(255, 255, 255, 0.1);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-source: #3b82f6;
            --accent-skill: #f43f5e;
            --accent-rule: #f59e0b;
            --accent-edge: #64748b;
        }}

        body {{
            font-family: 'Inter', sans-serif;
            margin: 0;
            padding: 0;
            background-color: var(--bg-base);
            color: var(--text-primary);
            overflow: hidden;
            height: 100vh;
            width: 100vw;
        }}

        #mynetwork {{
            width: 100%;
            height: 100%;
            outline: none;
        }}

        .ui-panel {{
            position: absolute;
            z-index: 100;
            background: var(--bg-panel);
            backdrop-filter: blur(16px) saturate(180%);
            -webkit-backdrop-filter: blur(16px) saturate(180%);
            border: 1px solid var(--border-panel);
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
        }}

        .header {{
            top: 24px;
            left: 24px;
            max-width: 400px;
        }}

        .header h1 {{
            margin: 0;
            font-size: 1.25rem;
            font-weight: 600;
            letter-spacing: -0.025em;
            background: linear-gradient(to right, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .header p {{
            margin: 0.5rem 0 0;
            font-size: 0.875rem;
            color: var(--text-secondary);
            line-height: 1.5;
        }}

        .legend {{
            bottom: 24px;
            left: 24px;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }}

        .legend-title {{
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-secondary);
            margin-bottom: 0.25rem;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            font-size: 0.875rem;
            font-weight: 500;
        }}

        .dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 12px;
            box-shadow: 0 0 8px currentColor;
        }}

        .empty-state {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
            max-width: 400px;
            pointer-events: none;
        }}

        .empty-state-icon {{
            font-size: 3rem;
            margin-bottom: 1.5rem;
            opacity: 0.2;
        }}

        .empty-state h2 {{
            font-size: 1.5rem;
            font-weight: 600;
            margin: 0;
            color: var(--text-secondary);
        }}

        .empty-state p {{
            margin: 1rem 0 0;
            color: #475569;
            font-size: 0.95rem;
            line-height: 1.6;
        }}

        /* Scrollbar styling for tooltips if any */
        ::-webkit-scrollbar {{ width: 6px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 10px; }}
    </style>
</head>
<body>
    <div class="ui-panel header">
        <h1>Cortex Architecture Map</h1>
        <p>프로젝트 아키텍처 및 지식 관계를 시각화합니다. 코드 간의 의존성과 규칙 문서의 활용도를 실시간으로 추적합니다.</p>
        <div style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid rgba(255,255,255,0.05); font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #475569;">
            NODES: {node_count} | EDGES: {edge_count} | {now_str}
        </div>
    </div>

    <div class="ui-panel legend">
        <div class="legend-title">System Layer</div>
        <div class="legend-item"><div class="dot" style="color: var(--accent-source); background: var(--accent-source);"></div> SOURCE (Core Logic)</div>
        <div class="legend-item"><div class="dot" style="color: var(--accent-rule); background: var(--accent-rule);"></div> RULE (Protocol & Policy)</div>
        <div class="legend-item"><div class="dot" style="color: var(--accent-skill); background: var(--accent-skill);"></div> SKILL (Agent Memory)</div>
    </div>

    <div id="mynetwork"></div>

    {empty_state_html}

    <script type="text/javascript">
        const nodesData = {nodes_json};
        const edgesData = {edges_json};

        if (nodesData.length > 0) {{
            const nodes = new vis.DataSet(nodesData);
            const edges = new vis.DataSet(edgesData);

            const container = document.getElementById('mynetwork');
            const data = {{ nodes, edges }};
            const options = {{
                nodes: {{
                    shape: 'dot',
                    size: 16,
                    font: {{ 
                        size: 12, 
                        color: '#f1f5f9',
                        face: 'Inter',
                        strokeWidth: 4,
                        strokeColor: '#0f172a'
                    }},
                    borderWidth: 2,
                    shadow: {{ enabled: true, color: 'rgba(0,0,0,0.5)', size: 10, x: 0, y: 5 }}
                }},
                edges: {{
                    width: 2,
                    color: {{ 
                        color: '#475569', 
                        highlight: '#3b82f6',
                        hover: '#94a3b8',
                        opacity: 0.6 
                    }},
                    arrows: {{ to: {{ enabled: true, scaleFactor: 0.6, type: 'arrow' }} }},
                    smooth: {{ type: 'curvedCW', roundness: 0.15 }},
                    selectionWidth: 3
                }},
                physics: {{
                    forceAtlas2Based: {{
                        gravitationalConstant: -100,
                        centralGravity: 0.005,
                        springLength: 200,
                        springConstant: 0.08
                    }},
                    solver: 'forceAtlas2Based',
                    stabilization: {{ iterations: 150 }}
                }},
                interaction: {{
                    hover: true,
                    tooltipDelay: 200,
                    hideEdgesOnDrag: true
                }}
            }};

            const network = new vis.Network(container, data, options);
            
            network.on("stabilizationIterationsDone", function () {{
                network.setOptions({{ physics: false }});
            }});
        }}
    </script>
</body>
</html>
"""

EMPTY_STATE_CONTENT = """
    <div class="empty-state">
        <div class="empty-state-icon">🕸️</div>
        <h2>연결된 노드가 없습니다</h2>
        <p>현재 워크스페이스에 연결선(Edge)을 가진 유효한 노드가 존재하지 않습니다. 실제 코드를 작성하거나 규칙을 명시적으로 연결하면 아키텍처 지도가 그려집니다.</p>
    </div>
"""

def generate_graph_viz(workspace_path):
    db_path = os.path.join(workspace_path, ".agents", "cortex_data", "index.db")
    if not os.path.exists(db_path):
        return None, "Database not found"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # 1. 필터링 로직: 연결된 엣지가 있는 노드만 추출
        # 또한 file_path가 .agents/scripts/로 시작하는 것은 제외 (설정 반영)
        query = """
            SELECT id, name, fqn, type, category, module, file_path
            FROM nodes 
            WHERE id IN (
                SELECT source_id FROM edges 
                UNION 
                SELECT target_id FROM edges
            )
            AND file_path NOT LIKE '.agents/scripts/%'
        """
        nodes_rows = conn.execute(query).fetchall()
        
        # 2. 엣지 추출 (필터링된 노드 간의 엣지만)
        node_ids = [row['id'] for row in nodes_rows]
        placeholders = ','.join(['?'] * len(node_ids))
        
        edges_rows = []
        if node_ids:
            edges_query = f"SELECT source_id, target_id, type FROM edges WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})"
            edges_rows = conn.execute(edges_query, node_ids + node_ids).fetchall()
        
        conn.close()

        nodes_list = []
        for row in nodes_rows:
            color = "#94a3b8" 
            if row['category'] == 'SOURCE': color = "#3b82f6"
            elif row['category'] == 'SKILL': color = "#f43f5e"
            elif row['category'] == 'RULE': color = "#f59e0b"
            
            nodes_list.append({
                "id": row['id'],
                "label": row['name'],
                "title": f"FQN: {row['fqn']}<br>Type: {row['type']}<br>File: {row['file_path']}",
                "color": color,
                "borderWidth": 2
            })

        edges_list = []
        for row in edges_rows:
            edges_list.append({
                "from": row['source_id'],
                "to": row['target_id'],
                "label": "" # Less noise
            })

        viz_dir = os.path.join(workspace_path, ".agents", "history", "viz")
        os.makedirs(viz_dir, exist_ok=True)
        
        output_path = os.path.join(viz_dir, "graph_viz.html")
        
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        html_content = HTML_TEMPLATE.format(
            vis_js_cdn=VIS_JS_CDN,
            now_str=now,
            node_count=len(nodes_list),
            edge_count=len(edges_list),
            nodes_json=json.dumps(nodes_list),
            edges_json=json.dumps(edges_list),
            empty_state_html=EMPTY_STATE_CONTENT if len(nodes_list) == 0 else ""
        )
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
            
        return output_path, None
        
    except Exception as e:
        return None, str(e)

if __name__ == "__main__":
    import sys
    workspace = sys.argv[1] if len(sys.argv) > 1 else "."
    path, err = generate_graph_viz(workspace)
    if err:
        print(f"Error: {err}")
    else:
        print(f"Viz generated at: {path}")
