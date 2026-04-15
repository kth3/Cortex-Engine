import re

path = "/home/developer/.gemini/tmp/my-project/cortex-temp/scripts/cortex_mcp.py"
with open(path, "r") as f:
    content = f.read()

# Remove imports
content = re.sub(r'from cortex\.impact import .*?\n', '', content)
content = re.sub(r'from cortex\.visualizer import .*?\n', '', content)

# Remove pc_impact_graph definition
content = re.sub(r'def pc_impact_graph\(.*?\):(?:.|\n)*?(?=def pc_|\Z)', '', content)

# Remove pc_viz definition
content = re.sub(r'def pc_viz\(.*?\):(?:.|\n)*?(?=def pc_|\Z)', '', content)

# Remove from TOOLS array
content = re.sub(r'\s*\{"name": "pc_impact_graph".*?\},', '', content, flags=re.DOTALL)
content = re.sub(r'\s*\{"name": "pc_viz".*?\},', '', content, flags=re.DOTALL)

# Remove from dispatch block
content = re.sub(r'\s*elif name == "pc_impact_graph":.*?\n', '\n', content)
content = re.sub(r'\s*elif name == "pc_viz":.*?\n', '\n', content)

with open(path, "w") as f:
    f.write(content)

