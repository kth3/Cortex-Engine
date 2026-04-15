import os
import kuzu
from typing import Optional

def get_graph_db_path(workspace: str) -> str:
    """kuzu DB 경로 반환"""
    if workspace.endswith(".cortex"):
        base_dir = workspace
    else:
        base_dir = os.path.join(workspace, ".cortex")
    
    db_dir = os.path.join(base_dir, "graph.kuzu")
    os.makedirs(db_dir, exist_ok=True)
    return db_dir

class GraphDB:
    def __init__(self, workspace: str):
        self.db_path = get_graph_db_path(workspace)
        self.db = kuzu.Database(self.db_path)
        self.conn = kuzu.Connection(self.db)
        self._init_schema()

    def _init_schema(self):
        """Kuzu 그래프 스키마 생성"""
        # Node Tables
        try:
            self.conn.execute("CREATE NODE TABLE IF NOT EXISTS Module (name STRING, file_path STRING, PRIMARY KEY (name))")
            self.conn.execute("CREATE NODE TABLE IF NOT EXISTS Function (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))")
            self.conn.execute("CREATE NODE TABLE IF NOT EXISTS Class (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))")
            
            # Edge Tables
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Imports (FROM Module TO Module)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Calls (FROM Function TO Function, FROM Function TO Class, FROM Class TO Function, FROM Class TO Class)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Defines (FROM Module TO Function, FROM Module TO Class)")
        except Exception as e:
            # Table exists error or similar
            pass

    def execute(self, query: str, parameters: dict = None):
        """Cypher 쿼리 실행"""
        if parameters is None:
            parameters = {}
        return self.conn.execute(query, parameters)
