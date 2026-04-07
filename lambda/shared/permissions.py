"""Permission constants."""
API_PERMISSIONS = {
    "list_agents": "viewer", "get_agent": "viewer",
    "create_agent": "editor", "update_agent": "editor",
    "delete_agent": "editor", "deploy_agent": "editor",
    "publish_agent": "admin",
    "list_skills": "viewer", "get_skill": "viewer",
    "create_skill": "editor", "update_skill": "editor",
    "delete_skill": "editor", "approve_skill": "admin",
    "list_tools": "viewer", "get_tool": "viewer",
    "create_tool": "editor", "update_tool": "editor",
    "delete_tool": "editor",
    "list_secrets": "admin", "set_secret": "admin", "delete_secret": "admin",
    "invoke_meta_agent": "editor", "invoke_agent": "viewer",
    "upload_image": "viewer", "upload_attachment": "viewer",
}
