# Claude Code Instruction Template (Relay Mode)

All tasks MUST follow the protocols defined in `.agents/`. 
Operate under the 3-agent coordination system (Antigravity, Gemini, Claude).

## 1. Pre-flight Check (Mandatory)
Before starting ANY task, check the board and acquire the lock:
1. `python3 .agents/scripts/relay.py status` (Read the handoff message from the previous agent)
2. `python3 .agents/scripts/relay.py acquire CLAUDE "summary of your task"`

## 2. Build and Test
Refer to `.agents/rules/` for detailed coding standards.
Use project-specific commands provided via MCP for build and verification.

## 3. Post-flight Handoff (Mandatory)
Once the task is complete, release the lock and leave a note:
`python3 .agents/scripts/relay.py release CLAUDE [GEMINI|ANTIGRAVITY] "detailed task summary and next steps"`

## 4. Shared Memory
Check `pc_capsule` at the beginning of each session to sync with the board's handoff message.
Save architectural insights using `pc_save_observation` to help the next agent.
