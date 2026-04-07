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
    <title>Cortex Knowledge Graph Visualization</title>
    <script type="text/javascript" src="{vis_js_cdn}"></script>
    <style type="text/css">
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 0;
            background-color: #0f172a;
            color: #f8fafc;
            overflow: hidden;
        }}
        #mynetwork {{
            width: 100vw;
            height: 100vh;
            background-color: #0f172a;
        }}
        .header {{
            position: absolute;
            top: 20px;
            left: 20px;
            z-index: 10;
            background: rgba(30, 41, 59, 0.8);
            padding: 15px 25px;
            border-radius: 12px;
            border: 1px solid #334155;
            backdrop-filter: blur(8px);
            pointer-events: none;
        }}
        .header h1 {{ margin: 0; font-size: 1.5rem; color: #38bdf8; }}
        .header p {{ margin: 5px 0 0; font-size: 0.9rem; opacity: 0.8; }}
        .legend {{
            position: absolute;
            bottom: 20px;
            left: 20px;
            z-index: 10;
            background: rgba(30, 41, 59, 0.8);
            padding: 15px;
            border-radius: 12px;
            border: 1px solid #334155;
            font-size: 0.85rem;
        }}
        .legend-item {{ display: flex; align-items: center; margin-bottom: 5px; }}
        .color-box {{ width: 15px; height: 15px; border-radius: 3px; margin-right: 10px; }}
        .controls {{
            position: absolute;
            top: 20px;
            right: 20px;
            z-index: 10;
            background: rgba(30, 41, 59, 0.8);
            padding: 15px;
            border-radius: 12px;
            border: 1px solid #334155;
        }}
        button {{
            background: #38bdf8;
            color: #0f172a;
            border: none;
            padding: 8px 15px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
        }}
        button:hover {{ background: #7dd3fc; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Cortex Knowledge Galaxy</h1>
        <p>프로젝트 지식 및 코드 관계 시각화 (Generated at: {now_str})</p>
        <p>Nodes: {node_count} | Edges: {edge_count}</p>
    </div>

    <div class="legend">
        <div class="legend-item"><div class="color-box" style="background: #38bdf8;"></div>SOURCE (Code)</div>
        <div class="legend-item"><div class="color-box" style="background: #f472b6;"></div>SKILL (Doc)</div>
        <div class="legend-item"><div class="color-box" style="background: #fbbf24;"></div>RULE (Protocol)</div>
        <div class="legend-item"><div class="color-box" style="background: #94a3b8;"></div>OTHER</div>
    </div>

    <div id="mynetwork"></div>

    <script type="text/javascript">
        const nodes = new vis.DataSet({nodes_json});
        const edges = new vis.DataSet({edges_json});

        const container = document.getElementById('mynetwork');
        const data = {{ nodes, edges }};
        const options = {{
            nodes: {{
                shape: 'dot',
                size: 16,
                font: {{ size: 12, color: '#ffffff' }},
                borderWidth: 2,
                shadow: true
            }},
            edges: {{
                width: 1,
                color: {{ color: '#475569', opacity: 0.6, hover: '#38bdf8' }},
                arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
                smooth: {{ type: 'continuous' }}
            }},
            physics: {{
                stabilization: false,
                barnesHut: {{
                    gravitationalConstant: -10000,
                    springLength: 200,
                    springConstant: 0.04
                }}
            }},
            interaction: {{
                hover: true,
                tooltipDelay: 200
            }}
        }};

        const network = new vis.Network(container, data, options);
    </script>
</body>
</html>
"""

def generate_graph_viz(workspace_path):
    db_path = os.path.join(workspace_path, ".agents", "cortex_data", "index.db")
    if not os.path.exists(db_path):
        return None, "Database not found"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # 노드 데이터 추출
        nodes_rows = conn.execute("SELECT id, name, fqn, type, category, module FROM nodes").fetchall()
        # 엣지 데이터 추출
        edges_rows = conn.execute("SELECT source_id, target_id, type FROM edges").fetchall()
        conn.close()

        nodes_list = []
        for row in nodes_rows:
            # 카테고리에 따른 색상 지정
            color = "#94a3b8" # Default
            if row['category'] == 'SOURCE': color = "#38bdf8"
            elif row['category'] == 'SKILL': color = "#f472b6"
            elif row['category'] == 'RULE': color = "#fbbf24"
            
            nodes_list.append({
                "id": row['id'],
                "label": row['name'],
                "title": f"FQN: {row['fqn']}<br>Type: {row['type']}<br>Module: {row['module']}",
                "color": color,
                "group": row['module']
            })

        edges_list = []
        for row in edges_rows:
            edges_list.append({
                "from": row['source_id'],
                "to": row['target_id'],
                "label": row['type'] if row['type'] != 'CALLS' else ""
            })

        viz_dir = os.path.join(workspace_path, ".agents", "history", "viz")
        os.makedirs(viz_dir, exist_ok=True)
        
        output_path = os.path.join(viz_dir, "graph_viz.html")
        
        html_content = HTML_TEMPLATE.format(
            vis_js_cdn=VIS_JS_CDN,
            now_str=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            node_count=len(nodes_list),
            edge_count=len(edges_list),
            nodes_json=json.dumps(nodes_list),
            edges_json=json.dumps(edges_list)
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
