from agromech_api.agent_router import route_question


def test_maintenance_period_question_routes_text_only() -> None:
    decision = route_question("MG2004 液压油多久换一次？", parsed_query=None, image_context=None)

    assert decision["route"] == "text_only"
    assert decision["source"] == "rule"


def test_visual_wording_routes_text_visual() -> None:
    decision = route_question("这张图纸里液压泵在页面哪个位置？", parsed_query=None, image_context=None)

    assert decision["route"] == "text_visual"
    assert "visual" in decision["reason"]


def test_uploaded_image_routes_text_visual() -> None:
    decision = route_question("这个部件怎么检查？", parsed_query=None, image_context={"description": "pump"})

    assert decision["route"] == "text_visual"
