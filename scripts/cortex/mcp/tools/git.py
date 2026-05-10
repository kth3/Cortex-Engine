from cortex import git_analyzer as pc_git_mod

def call_pc_git_log(ctx, args):
    try:
        history = pc_git_mod.get_file_history(ctx.workspace, args["file_path"], args.get("limit", 5))
        return history
    except Exception as e:
        return {"error": str(e)}
