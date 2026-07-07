from apps.chat_data_app.routers import uploaded_files as uploaded_file_routes
from utils.error_codes import ErrorCode


def test_uploaded_file_info_and_list_use_business_response(client, monkeypatch):
    async def fake_get_file_info(file_id):
        return {
            "file_id": file_id,
            "status": "completed",
            "original_filename": "demo.txt",
        }

    async def fake_list_files_by_thread(thread_id):
        return [{"file_id": "file_1", "thread_id": thread_id}]

    monkeypatch.setattr(uploaded_file_routes, "get_file_info", fake_get_file_info)
    monkeypatch.setattr(uploaded_file_routes, "list_files_by_thread", fake_list_files_by_thread)

    info_response = client.get("/api/chatdata/uploaded-files/file_1")
    list_response = client.get("/api/chatdata/uploaded-files/thread/thread_1")

    assert info_response.status_code == 200
    assert info_response.json()["status"] == "success"
    assert info_response.json()["data"]["file_id"] == "file_1"
    assert list_response.status_code == 200
    assert list_response.json()["status"] == "success"
    assert list_response.json()["data"]["files"] == [{"file_id": "file_1", "thread_id": "thread_1"}]


def test_uploaded_file_error_branches_use_business_response(client, monkeypatch):
    async def fake_missing_file(_file_id):
        return None

    async def fake_delete_file(_file_id):
        return False

    monkeypatch.setattr(uploaded_file_routes, "get_file_info", fake_missing_file)
    monkeypatch.setattr(uploaded_file_routes, "delete_file", fake_delete_file)

    info_response = client.get("/api/chatdata/uploaded-files/missing")
    delete_response = client.delete("/api/chatdata/uploaded-files/missing")

    assert info_response.status_code == 404
    assert info_response.json()["status"] == "error"
    assert info_response.json()["code"] == ErrorCode.HTTP_NOT_FOUND
    assert delete_response.status_code == 404
    assert delete_response.json()["status"] == "error"
    assert delete_response.json()["code"] == ErrorCode.HTTP_NOT_FOUND


def test_chatdata_http_and_validation_errors_use_business_response(client):
    missing_response = client.get("/api/chatdata/not-exist")
    validation_response = client.post(
        "/api/chatdata/uploaded-files/upload",
        data={"thread_id": "thread_1"},
    )

    assert missing_response.status_code == 404
    assert missing_response.json()["status"] == "error"
    assert missing_response.json()["code"] == ErrorCode.HTTP_NOT_FOUND
    assert validation_response.status_code == 422
    assert validation_response.json()["status"] == "error"
    assert validation_response.json()["code"] == ErrorCode.HTTP_VALIDATION_ERROR


def test_uploaded_file_download_success_keeps_binary_response(client, monkeypatch):
    async def fake_get_file_info(_file_id):
        return {"file_type": "txt", "original_filename": "demo.txt"}

    async def fake_get_file_content(_file_id):
        return b"hello"

    monkeypatch.setattr(uploaded_file_routes, "get_file_info", fake_get_file_info)
    monkeypatch.setattr(uploaded_file_routes, "get_file_content", fake_get_file_content)

    response = client.get("/api/chatdata/uploaded-files/file_1/content")

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["content-type"].startswith("text/plain")
