from fastapi import FastAPI
from fastapi.testclient import TestClient

from agromech_api.errors import AppError, ErrorCode, register_error_handlers


def app_with_error_routes() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

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
