from agromech_api.config import Settings
from agromech_api.service_adapters import ServiceTimeouts


def test_external_service_timeouts_are_loaded_from_settings() -> None:
    settings = Settings(
        llm_request_timeout_seconds=17,
        retrieval_timeout_seconds=11,
        dependency_connect_timeout_seconds=3,
    )

    timeouts = ServiceTimeouts.from_settings(settings)

    assert timeouts.llm_seconds == 17
    assert timeouts.retrieval_seconds == 11
    assert timeouts.connection_seconds == 3
