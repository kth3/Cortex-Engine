from cortex.paths import settings_paths

def load_settings(workspace: str) -> dict:
    """Cortex settings.yaml 및 settings.local.yaml 파일 로드 및 병합"""
    settings_path, local_path = settings_paths(workspace)
    
    settings = {}
    import yaml
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = yaml.safe_load(f) or {}
        except Exception:
            pass

    if local_path.exists():
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                local_settings = yaml.safe_load(f) or {}
                for k, v in local_settings.items():
                    if k == "indexing_rules" and isinstance(v, dict) and "indexing_rules" in settings:
                        for sub_k, sub_v in v.items():
                            if sub_k == "index_roots":
                                settings["indexing_rules"][sub_k] = sub_v
                            elif isinstance(sub_v, list) and sub_k in settings["indexing_rules"] and isinstance(settings["indexing_rules"][sub_k], list):
                                settings["indexing_rules"][sub_k] = list(dict.fromkeys(settings["indexing_rules"][sub_k] + sub_v))
                            else:
                                settings["indexing_rules"][sub_k] = sub_v
                    else:
                        settings[k] = v
        except Exception:
            pass
            
    return settings
