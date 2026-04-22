import kuzu, json, os

# 현재 스크립트 위치를 기준으로 경로 설정
base_dir = os.path.dirname(os.path.abspath(__file__))
# scripts/ 폴더에 있으므로 한 단계 위로 올라가서 data/ 폴더 탐색
db_path = os.path.join(base_dir, '..', 'data', 'graph_db_store')
html_path = os.path.join(base_dir, '..', 'data', 'kuzu_viewer.html')

db = kuzu.Database(db_path)
conn = kuzu.Connection(db)
nodes, edges = [], []

def get_nodes(ntype, q):
    try:
        res = conn.execute(q)
        while res.has_next():
            row = res.get_next()
            nodes.append({
                "id": str(row[0]), "label": str(row[1]), "group": ntype,
                "title": str(row[2]) if len(row)>2 and row[2] else "",
                "shape": "dot", "size": 15 if ntype == "Function" else 25
            })
    except Exception as e: print(f"Err {ntype}: {e}")

get_nodes("Function", "MATCH (n:Function) RETURN n.fqn, n.name, n.file_path")
get_nodes("Class", "MATCH (n:Class) RETURN n.fqn, n.name, n.file_path")
get_nodes("Module", "MATCH (n:Module) RETURN n.name as id, n.name as label, n.file_path")
get_nodes("External", "MATCH (n:External) RETURN n.fqn, n.name, '' as file_path")

for etype in ["Imports", "Calls", "Defines", "Contains"]:
    try:
        q = f"MATCH (a)-[r:{etype}]->(b) RETURN coalesce(a.fqn, a.name), coalesce(b.fqn, b.name)"
        res = conn.execute(q)
        while res.has_next():
            row = res.get_next()
            if row[0] and row[1]:
                edges.append({"from": str(row[0]), "to": str(row[1]), "label": etype, "arrows": "to"})
    except: pass

with open(html_path, "w") as f:
    f.write(f'''<!DOCTYPE html><html><head>
    <script src="./vis-network.min.js"></script>
    <style>body{{margin:0;background:#1a1a1a;color:#fff}}#nw{{width:100vw;height:100vh}}</style></head>
    <body><div id="nw"></div><script>
    var tk = {{nodes: new vis.DataSet({json.dumps(nodes)}), edges: new vis.DataSet({json.dumps(edges)})}};
    new vis.Network(document.getElementById('nw'), tk, {{
        nodes: {{
            font: {{color:'#fff', size:12}},
            shadow: true,
            borderWidth: 2
        }},
        edges: {{
            color: {{color: '#555', highlight: '#88aaff'}},
            smooth: {{type:'continuous'}}
        }},
        groups: {{
            Module: {{color: {{background: '#2B7CE9', border: '#1A53A0'}}}},
            Class: {{color: {{background: '#E09F3E', border: '#A67123'}}}},
            Function: {{color: {{background: '#4CAF50', border: '#2E7D32'}}}},
            External: {{color: {{background: '#F06292', border: '#C2185B'}}, shape: 'square'}}
        }},
        physics: {{
            solver: 'forceAtlas2Based',
            forceAtlas2Based: {{
                gravitationalConstant: -100,
                springLength: 100,
                springConstant: 0.08,
                avoidOverlap: 0.5
            }},
            stabilization: {{
                enabled: true,
                iterations: 50,
                updateInterval: 10
            }}
        }}
    }});
    </script></body></html>''')
