from enum import Enum


class ComplaintEventType(str, Enum):
    """
    SSE event types emitted by the /complaint/init_analysis pipeline.

    Values are collected from the streaming payloads produced during:
    - Upload/OCR/summary steps (task 0/1/2) workflow streaming
    - Detailed item analysis (task 3) workflow and retry events
    """

    # Auto-generated conversation title for the uploaded complaint
    CONVERSATION_TITLE = "conversation_title"

    # Workflow stream events (task2/task3)
    WORKFLOW_STARTED = "workflow_started"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    WORKFLOW_FINISHED = "workflow_finished"
    MESSAGE = "message"
    MESSAGE_END = "message_end"

    # Task3 retry notification when downstream processing is retried
    TASK3_RETRYING = "task3_retrying"


def normalize_event_name(raw: str | None) -> str | None:
    """
    Map a raw event name to a defined ComplaintEventType when possible.
    Falls back to the original string to remain backward compatible with
    upstream workflow event labels.
    """
    if not raw:
        return raw
    try:
        return ComplaintEventType(raw).value
    except ValueError:
        return raw
