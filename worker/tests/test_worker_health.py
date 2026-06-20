from agromech_worker.main import health_status


def test_worker_health_status() -> None:
    assert health_status() == {"status": "ok", "service": "worker"}

