import pytest

from apps.chat_data_app.services.workspace_path_policy import WORKSPACE_PATH_POLICY


def test_policy_normalizes_shared_aliases_and_classifies_special_paths():
    workspace = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/workspace/semantic/order.yaml")
    outputs = WORKSPACE_PATH_POLICY.parse("outputs/.tool-offload/result.json")
    uploads = WORKSPACE_PATH_POLICY.parse("/uploads/source.csv")
    skill = WORKSPACE_PATH_POLICY.parse("/mnt/skills/chart-processing/SKILL.md")

    assert workspace.normalized == "workspace/semantic/order.yaml"
    assert workspace.is_semantic_model is True
    assert outputs.is_tool_result is True
    assert uploads.kind == "uploads"
    assert skill.is_skill is True
    assert WORKSPACE_PATH_POLICY.to_sandbox_path(workspace) == (
        "/mnt/user-data/workspace/semantic/order.yaml"
    )
    assert WORKSPACE_PATH_POLICY.to_sandbox_path(skill) == (
        "/mnt/skills/chart-processing/SKILL.md"
    )


@pytest.mark.parametrize(
    "path",
    ["../secret.txt", "/etc/passwd", "workspace/../secret.txt", "workspace\\x00bad.txt"],
)
def test_policy_rejects_escape_and_invalid_paths(path):
    with pytest.raises(ValueError):
        WORKSPACE_PATH_POLICY.parse(path)


def test_policy_keeps_special_directory_write_rules_in_one_place():
    tool_result = WORKSPACE_PATH_POLICY.parse("outputs/.tool-offload/result.json")
    semantic = WORKSPACE_PATH_POLICY.parse("workspace/semantic/order.yaml")
    upload = WORKSPACE_PATH_POLICY.parse("uploads/source.csv")

    with pytest.raises(ValueError, match="tool-offload"):
        WORKSPACE_PATH_POLICY.assert_writable(tool_result)
    with pytest.raises(ValueError, match="只读快照"):
        WORKSPACE_PATH_POLICY.assert_writable(semantic)
    with pytest.raises(ValueError, match="workspace/ 或 outputs/"):
        WORKSPACE_PATH_POLICY.assert_writable(upload)
    WORKSPACE_PATH_POLICY.assert_writable(
        semantic,
        allow_managed_semantic_model_write=True,
    )
