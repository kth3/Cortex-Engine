import kuzu, json, os

# 현재 스크립트 위치를 기준으로 경로 설정
base_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(base_dir, 'graph.kuzu')
html_path = os.path.join(base_dir, 'kuzu_viewer.html')

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
    f.write(f'''<!DOCTYPE html><html><head><script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>body{{margin:0;background:#1a1a1a;color:#fff}}#nw{{width:100vw;height:100vh}}</style></head>
    <body><div id="nw"></div><script>
    var tk = {{nodes: new vis.DataSet({json.dumps(nodes)}), edges: new vis.DataSet({json.dumps(edges)})}};
    new vis.Network(document.getElementById('nw'), tk, {{
        nodes:{{font:{{color:'#fff', size:14}}, shadow:true}}, 
        edges:{{color:'#888', smooth:{{type:'continuous'}}}},
        physics: {{ forceAtlas2Based: {{ gravitationalConstant: -100, springLength: 200 }}, solver: 'forceAtlas2Based' }}
    }});
    </script></body></html>''')
