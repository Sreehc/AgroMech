from fastapi import FastAPI
from fastapi.testclient import TestClient

from agromech_api.errors import AppError, ErrorCode, register_error_handlers


def app_with_error_routes() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.middleware("http")
    async def attach_trace_id(request, call_next):
        request.state.trace_id = request.headers.get("X-Trace-Id")
        return await call_next(request)

    @app.get("/unsupported")
    def unsupported() -> None:
        raise AppError(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            "Unsupported file type",
            status_code=415,
            details={"extension": ".exe"},
        )

    @app.get("/http-error")
    def http_error() -> None:
        raise AppError(ErrorCode.FORBIDDEN, "Forbidden", status_code=403)

    @app.get("/timeout")
    def timeout_error() -> None:
        raise TimeoutError("socket timed out")

    @app.get("/unexpected")
    def unexpected_error() -> None:
        raise RuntimeError("boom")

    return app


def test_app_error_uses_uniform_response_shape() -> None:
    response = TestClient(app_with_error_routes()).get("/unsupported")

    assert response.status_code == 415
    assert response.json() == {
        "error": {
            "code": "unsupported_file_type",
            "message": "Unsupported file type",
            "details": {"extension": ".exe"},
            "trace_id": None,
        }
    }


def test_not_found_uses_uniform_response_shape() -> None:
    response = TestClient(app_with_error_routes()).get("/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["message"] == "Not Found"


def test_required_error_codes_are_defined() -> None:
    assert {code.value for code in ErrorCode}.issuperset(
        {
            "unauthorized",
            "forbidden",
            "unsupported_file_type",
            "file_too_large",
            "timeout",
        }
    )


def test_timeout_error_uses_uniform_timeout_response() -> None:
    response = TestClient(app_with_error_routes(), raise_server_exceptions=False).get("/timeout")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "timeout"
    assert response.json()["error"]["message"] == "Request timed out"


def test_unexpected_error_uses_uniform_internal_error_response_with_trace_id() -> None:
    response = TestClient(app_with_error_routes(), raise_server_exceptions=False).get(
        "/unexpected",
        headers={"X-Trace-Id": "trace-test-123"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "details": None,
            "trace_id": "trace-test-123",
        }
    }
