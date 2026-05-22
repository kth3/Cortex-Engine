use serde_json::{Map, Value};
use std::sync::LazyLock;

fn object(entries: Vec<(&'static str, Value)>) -> Value {
    let mut map = Map::new();
    for (key, value) in entries {
        map.insert(key.to_string(), value);
    }
    Value::Object(map)
}

fn string_property(
    description: Option<&'static str>,
    enum_values: Option<&[&'static str]>,
    default: Option<Value>,
) -> Value {
    let mut entries = vec![("type", Value::String("string".to_string()))];
    if let Some(description) = description {
        entries.push(("description", Value::String(description.to_string())));
    }
    if let Some(enum_values) = enum_values {
        entries.push((
            "enum",
            Value::Array(
                enum_values
                    .iter()
                    .map(|value| Value::String((*value).to_string()))
                    .collect(),
            ),
        ));
    }
    if let Some(default) = default {
        entries.push(("default", default));
    }
    object(entries)
}

fn integer_property(description: Option<&'static str>, default: Option<Value>) -> Value {
    let mut entries = vec![("type", Value::String("integer".to_string()))];
    if let Some(description) = description {
        entries.push(("description", Value::String(description.to_string())));
    }
    if let Some(default) = default {
        entries.push(("default", default));
    }
    object(entries)
}

fn boolean_property(description: Option<&'static str>, default: Option<Value>) -> Value {
    let mut entries = vec![("type", Value::String("boolean".to_string()))];
    if let Some(description) = description {
        entries.push(("description", Value::String(description.to_string())));
    }
    if let Some(default) = default {
        entries.push(("default", default));
    }
    object(entries)
}

fn array_string_property(description: Option<&'static str>) -> Value {
    let mut entries = vec![
        ("type", Value::String("array".to_string())),
        (
            "items",
            object(vec![("type", Value::String("string".to_string()))]),
        ),
    ];
    if let Some(description) = description {
        entries.push(("description", Value::String(description.to_string())));
    }
    object(entries)
}

fn object_property(description: Option<&'static str>) -> Value {
    let mut entries = vec![("type", Value::String("object".to_string()))];
    if let Some(description) = description {
        entries.push(("description", Value::String(description.to_string())));
    }
    object(entries)
}

fn input_schema(
    properties: Option<Vec<(&'static str, Value)>>,
    required: Option<&[&'static str]>,
) -> Value {
    let mut entries = vec![("type", Value::String("object".to_string()))];
    if let Some(properties) = properties {
        let mut props = Map::new();
        for (key, value) in properties {
            props.insert(key.to_string(), value);
        }
        entries.push(("properties", Value::Object(props)));
    }
    if let Some(required) = required {
        entries.push((
            "required",
            Value::Array(
                required
                    .iter()
                    .map(|value| Value::String((*value).to_string()))
                    .collect(),
            ),
        ));
    }
    object(entries)
}

fn tool(
    name: &'static str,
    description: &'static str,
    properties: Option<Vec<(&'static str, Value)>>,
    required: Option<&[&'static str]>,
) -> Value {
    object(vec![
        ("name", Value::String(name.to_string())),
        ("description", Value::String(description.to_string())),
        ("inputSchema", input_schema(properties, required)),
    ])
}

pub static TOOLS: LazyLock<Vec<Value>> = LazyLock::new(|| {
    vec![
        tool(
            "get_index_status",
            "Return Cortex index and database status: node/edge/file/memory counts and schema version. Use to verify the index is populated before running graph or search tools. Read-only.",
            None,
            None,
        ),
        tool(
            "search_context",
            "Search compact project context across code, documentation, and stored knowledge. Use this first when answering codebase questions, looking up implementations, or tracing design history. Returns a compact capsule with estimated token usage. Read-only. No side effects. Do not use for exact file editing; call read_file_with_hash before replace_exact_text.",
            Some(vec![
                (
                    "query",
                    string_property(
                        Some("Natural-language or keyword query describing what you are looking for."),
                        None,
                        None,
                    ),
                ),
                (
                    "token_budget",
                    integer_property(
                        Some("Maximum response size in approximate tokens (chars/4 estimate). Default 4000."),
                        Some(Value::from(4000)),
                    ),
                ),
            ]),
            Some(&["query"]),
        ),
        tool(
            "search_deep_context",
            "Run a comprehensive search combining code index, call graph, and memory for complex questions. Use when search_context returns insufficient context, or when the question requires cross-cutting code + architecture + decision history. When the code capsule result is sparse, automatically chains in related memory entries for broader context. Response includes capsule_chars for gauging result density; chained_memories is present when chaining triggered. Slower than search_context. Read-only. No side effects. Do not use for simple keyword lookups; prefer search_context for those.",
            Some(vec![
                (
                    "query",
                    string_property(
                        Some("Natural-language query for comprehensive cross-domain search."),
                        None,
                        None,
                    ),
                ),
                (
                    "limit",
                    integer_property(
                        Some("Maximum number of unified result items to return. Default 5."),
                        Some(Value::from(5)),
                    ),
                ),
            ]),
            Some(&["query"]),
        ),
        tool(
            "get_file_outline",
            "Return the structural outline of a file: classes, functions, methods, and key symbols — without reading the full content. Use before reading large files to decide which sections need full inspection. Read-only.",
            Some(vec![
                (
                    "file_path",
                    string_property(Some("Workspace-relative path to the file."), None, None),
                ),
                (
                    "detail",
                    string_property(
                        Some("Outline verbosity: 'minimal' (names only), 'standard' (signatures), 'detailed' (includes docstrings). Default 'standard'."),
                        Some(&["minimal", "standard", "detailed"]),
                        Some(Value::String("standard".to_string())),
                    ),
                ),
            ]),
            Some(&["file_path"]),
        ),
        tool(
            "read_file_with_hash",
            "Read the current content of a file and return its content hash. Always call this before replace_exact_text to obtain the exact current text. Read-only. The returned hash is used internally to detect concurrent modifications.",
            Some(vec![(
                "file_path",
                string_property(Some("Workspace-relative path to the file to read."), None, None),
            )]),
            Some(&["file_path"]),
        ),
        tool(
            "resolve_symbol",
            "Resolve a class, function, method, or partial symbol name into fully-qualified name (FQN) candidates. Uses three-stage lookup: exact FQN match → FTS keyword search → vector similarity search (when embeddings are available). Use before get_impact_graph or find_execution_path when the exact FQN is unknown. Read-only. Returns a list of candidates with fqn, kind, language, file_path, line, and match_reason (exact_fqn | fts_match | vector_match). If no matches are found, returns an empty list with a next_suggestion.",
            Some(vec![
                (
                    "name",
                    string_property(
                        Some("Symbol name to resolve. May be a short name, partial path, or exact FQN."),
                        None,
                        None,
                    ),
                ),
                (
                    "file_path",
                    string_property(
                        Some("Optional: narrow results to symbols defined in this file (workspace-relative)."),
                        None,
                        None,
                    ),
                ),
                (
                    "language",
                    string_property(
                        Some("Optional: narrow results to a specific language (e.g. 'python', 'typescript')."),
                        None,
                        None,
                    ),
                ),
                (
                    "limit",
                    integer_property(
                        Some("Maximum number of FQN candidates to return. Default 5."),
                        Some(Value::from(5)),
                    ),
                ),
            ]),
            Some(&["name"]),
        ),
        tool(
            "get_impact_graph",
            "Return callers, callees, or both for a given fully-qualified name (FQN) up to a specified depth. Use to understand the blast radius of a change or to trace who uses a symbol. If you do not know the exact FQN, call resolve_symbol first. Read-only. Response includes truncated, limit, returned_count, total_seen metadata.",
            Some(vec![
                (
                    "fqn",
                    string_property(
                        Some("Exact fully-qualified name of the symbol. Use resolve_symbol first if unknown."),
                        None,
                        None,
                    ),
                ),
                (
                    "direction",
                    string_property(
                        Some("Which edges to traverse: 'callers' (who calls this), 'callees' (what this calls), or 'both'. Default 'both'."),
                        Some(&["callers", "callees", "both"]),
                        Some(Value::String("both".to_string())),
                    ),
                ),
                (
                    "max_depth",
                    integer_property(
                        Some("Maximum traversal depth from the root symbol. Default 2."),
                        Some(Value::from(2)),
                    ),
                ),
                (
                    "max_nodes",
                    integer_property(
                        Some("Maximum number of nodes to return. Default 50."),
                        Some(Value::from(50)),
                    ),
                ),
            ]),
            Some(&["fqn"]),
        ),
        tool(
            "find_execution_path",
            "Find the call path between two symbols identified by their fully-qualified names (FQN). Use to understand how execution flows from one function to another. If you do not know the exact FQNs, call resolve_symbol first for each symbol. Read-only. Response includes path (list of FQNs), truncated, limit, returned_count, total_seen metadata.",
            Some(vec![
                (
                    "from_fqn",
                    string_property(
                        Some("FQN of the starting symbol. Use resolve_symbol first if unknown."),
                        None,
                        None,
                    ),
                ),
                (
                    "to_fqn",
                    string_property(
                        Some("FQN of the ending symbol. Use resolve_symbol first if unknown."),
                        None,
                        None,
                    ),
                ),
                (
                    "max_depth",
                    integer_property(
                        Some("Maximum path length in hops. Default 6."),
                        Some(Value::from(6)),
                    ),
                ),
                (
                    "max_nodes",
                    integer_property(
                        Some("Maximum nodes to explore during BFS. Default 200."),
                        Some(Value::from(200)),
                    ),
                ),
            ]),
            Some(&["from_fqn", "to_fqn"]),
        ),
        tool(
            "get_file_git_history",
            "Return the git commit history for a specific file. Use to understand when and why a file was changed. Read-only.",
            Some(vec![
                (
                    "file_path",
                    string_property(Some("Workspace-relative path to the file."), None, None),
                ),
                (
                    "limit",
                    integer_property(
                        Some("Maximum number of commits to return. Default 5."),
                        Some(Value::from(5)),
                    ),
                ),
            ]),
            Some(&["file_path"]),
        ),
        tool(
            "replace_exact_text",
            "Replace an exact text fragment in a file. Always call read_file_with_hash first to obtain the exact current file content. This is a write operation with side effects. Fails safely if old_content does not match the current file content exactly. Triggers after-edit hooks and records an edit event in the Cortex database.",
            Some(vec![
                (
                    "file_path",
                    string_property(Some("Workspace-relative path to the file to edit."), None, None),
                ),
                (
                    "old_content",
                    string_property(
                        Some("Exact text to replace. Must match the current file content character-for-character."),
                        None,
                        None,
                    ),
                ),
                ("new_content", string_property(Some("Replacement text."), None, None)),
            ]),
            Some(&["file_path", "old_content", "new_content"]),
        ),
        tool(
            "get_session_context",
            "Return a summary of recent decisions, patterns, and frequently-accessed knowledge to restore session context. Call at the start of a session when prior work context is needed. Read-only.",
            Some(vec![(
                "token_budget",
                integer_property(
                    Some("Maximum response size in approximate tokens (chars/4 estimate). Default 2000."),
                    Some(Value::from(2000)),
                ),
            )]),
            None,
        ),
        tool(
            "sync_session_memory",
            "Synchronize session state to persistent memory by scanning git status and recently modified files. Call at the end of a meaningful work session (code edits, design decisions, completed exploration). Side-effect: writes a session-sync memory record and updates memory.yaml. Not calling this will cause incomplete context restoration in the next session.",
            Some(vec![(
                "task_desc",
                string_property(Some("Brief description of work completed in this session."), None, None),
            )]),
            Some(&["task_desc"]),
        ),
        tool(
            "write_memory",
            "Write a keyed knowledge record to persistent memory. Side-effect: persists to the memory database and optionally promotes to markdown history files (decisions.md for 'decision'/'architecture' categories; patterns.md for 'pattern'/'convention'/'rule'/'protocol').",
            Some(vec![
                ("key", string_property(Some("Unique identifier for this memory record."), None, None)),
                (
                    "category",
                    string_property(
                        Some("Semantic category (e.g. 'decision', 'architecture', 'pattern', 'convention', 'rule', 'insight')."),
                        None,
                        None,
                    ),
                ),
                ("content", string_property(Some("The knowledge content to store."), None, None)),
                (
                    "tags",
                    array_string_property(Some("Optional list of searchable tags.")),
                ),
                (
                    "relationships",
                    object_property(Some("Optional relationship map (e.g. {'related_to': ['key1']}).")),
                ),
            ]),
            Some(&["key", "category", "content"]),
        ),
        tool(
            "consolidate_memory",
            "Merge multiple fragmented memory records into a single consolidated record. Side-effect when dry_run=false: deletes old_keys and writes the new consolidated record. dry_run=true (default) returns a preview of what would be deleted and written without making changes. Do not trigger automatically; only use when explicitly requested.",
            Some(vec![
                ("new_key", string_property(Some("Key for the consolidated memory record."), None, None)),
                ("category", string_property(Some("Category for the consolidated record."), None, None)),
                ("content", string_property(Some("Merged content for the consolidated record."), None, None)),
                (
                    "old_keys",
                    array_string_property(Some("List of existing memory keys to delete after consolidation.")),
                ),
                ("tags", array_string_property(Some("Optional tags for the consolidated record."))),
                (
                    "relationships",
                    object_property(Some("Optional relationship map for the consolidated record.")),
                ),
                (
                    "dry_run",
                    boolean_property(
                        Some("If true (default), return a preview without making any changes. Set to false to perform actual deletion and write."),
                        Some(Value::Bool(true)),
                    ),
                ),
            ]),
            Some(&["new_key", "category", "content", "old_keys"]),
        ),
        tool(
            "read_memory",
            "Read a single memory record by its exact key. Read-only. Use search_memory to find records when the exact key is unknown.",
            Some(vec![("key", string_property(Some("Exact key of the memory record to retrieve."), None, None))]),
            Some(&["key"]),
        ),
        tool(
            "save_observation",
            "Record a short observation or insight about code, decisions, or discoveries made during this session. Side-effect: writes to the observation log and triggers after-save hooks. Use after meaningful code edits, bug discoveries, or design decisions.",
            Some(vec![("content", string_property(Some("The observation content to record."), None, None))]),
            Some(&["content"]),
        ),
        tool(
            "search_memory",
            "Hybrid search over persistent knowledge, rules, and skills. Use to look up stored decisions, patterns, architecture notes, or project conventions. Read-only. Filter by category to narrow results (e.g. category='skill' or category='rule').",
            Some(vec![
                ("query", string_property(Some("Natural-language or keyword query."), None, None)),
                (
                    "category",
                    string_property(
                        Some("Optional: filter by category (e.g. 'skill', 'rule', 'decision', 'architecture', 'insight')."),
                        None,
                        None,
                    ),
                ),
            ]),
            Some(&["query"]),
        ),
        tool(
            "create_task_contract",
            "Create a task contract specifying the work scope, instructions, and files to be modified. Use before starting any task that involves 3 or more file changes or architectural decisions. Side-effect: writes contract to the board state and records an observation.",
            Some(vec![
                ("lane_id", string_property(Some("Lane identifier for multi-agent coordination."), None, None)),
                ("task_name", string_property(Some("Short name for this task."), None, None)),
                (
                    "instructions",
                    string_property(Some("Full task description and implementation instructions."), None, None),
                ),
                (
                    "files_to_modify",
                    array_string_property(Some("Optional list of workspace-relative file paths that will be modified.")),
                ),
            ]),
            Some(&["lane_id", "task_name", "instructions"]),
        ),
        tool(
            "manage_todo",
            "Add, check off, or clear items in the session todo list. Side-effect: modifies the todo list state. Use add to register a new task, check to mark it complete (requires task_id), clear to reset the list.",
            Some(vec![
                (
                    "action",
                    string_property(
                        Some("Action to perform: 'add' a new task, 'check' a task as done, or 'clear' the entire list."),
                        Some(&["add", "check", "clear"]),
                        None,
                    ),
                ),
                ("task", string_property(Some("Task description. Required when action='add'."), None, None)),
                ("task_id", string_property(Some("Task ID to mark complete. Required when action='check'."), None, None)),
            ]),
            Some(&["action"]),
        ),
    ]
});

pub fn list_tools() -> &'static [Value] {
    TOOLS.as_slice()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_matches_python_tool_count() {
        assert_eq!(TOOLS.len(), 19);
    }
}
