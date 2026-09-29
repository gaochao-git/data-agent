import pytest

from apps.chat_data_app.agent.workspace.workspace_path_policy import WORKSPACE_PATH_POLICY


def test_policy_accepts_only_canonical_forms_and_classifies_special_paths():
    workspace = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/workspace/semantic/order.yaml")
    outputs = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/outputs/.tool-offload/result.json")
    query_result = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/outputs/query_result/result.json")
    uploads = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/uploads/source.csv")
    skill = WORKSPACE_PATH_POLICY.parse("/mnt/skills/officecli/SKILL.md")

    assert workspace.normalized == "workspace/semantic/order.yaml"
    assert workspace.is_semantic_model is True
    assert outputs.is_tool_offload is True
    assert query_result.is_query_result is True
    assert uploads.kind == "uploads"
    assert skill.is_skill is True
    assert WORKSPACE_PATH_POLICY.to_sandbox_path(workspace) == ("/mnt/user-data/workspace/semantic/order.yaml")
    assert WORKSPACE_PATH_POLICY.to_sandbox_path(skill) == ("/mnt/skills/officecli/SKILL.md")


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "/etc/passwd",
        "workspace/../secret.txt",
        "workspace\\x00bad.txt",
        # 严进：裸别名、相对形式、./ 前缀不再是合法输入。
        "/workspace/a.txt",
        "/outputs/a.txt",
        "/uploads/a.txt",
        "workspace/a.txt",
        "outputs/a.txt",
        "uploads/a.txt",
        "./workspace/a.txt",
    ],
)
def test_policy_rejects_escape_and_non_canonical_paths(path):
    with pytest.raises(ValueError):
        WORKSPACE_PATH_POLICY.parse(path)


@pytest.mark.parametrize(
    "path",
    ["../secret.txt", "/etc/passwd", "workspace/../secret.txt", "workspace\\x00bad.txt"],
)
def test_policy_error_messages_name_the_canonical_prefix(path):
    with pytest.raises(ValueError, match="/mnt/user-data/"):
        WORKSPACE_PATH_POLICY.parse(path)


def test_policy_keeps_special_directory_write_rules_in_one_place():
    tool_result = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/outputs/.tool-offload/result.json")
    query_result = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/outputs/query_result/result.json")
    semantic = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/workspace/semantic/order.yaml")
    upload = WORKSPACE_PATH_POLICY.parse("/mnt/user-data/uploads/source.csv")

    with pytest.raises(ValueError, match="系统结果目录"):
        WORKSPACE_PATH_POLICY.assert_writable(tool_result)
    with pytest.raises(ValueError, match="系统结果目录"):
        WORKSPACE_PATH_POLICY.assert_writable(query_result)
    with pytest.raises(ValueError, match="只读快照"):
        WORKSPACE_PATH_POLICY.assert_writable(semantic)
    with pytest.raises(ValueError, match="只允许写入"):
        WORKSPACE_PATH_POLICY.assert_writable(upload)
    WORKSPACE_PATH_POLICY.assert_writable(
        semantic,
        allow_managed_semantic_model_write=True,
    )
