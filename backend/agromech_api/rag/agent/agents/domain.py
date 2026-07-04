from __future__ import annotations

from agromech_api.rag.agent.agents.base import AgentResult, agent_trace
from agromech_api.rag.agent.state import AgentState


DOMAIN_STRATEGIES = {
    "maintenance": {
        "domain_agent": "MaintenanceAgent",
        "required_sections": ["conclusion", "applicability", "maintenance_interval", "safety_reminder", "citations"],
        "answer_focus": "maintenance interval, fluid or consumable specification, model applicability, and source support",
    },
    "fault_diagnosis": {
        "domain_agent": "FaultDiagnosisAgent",
        "required_sections": ["conclusion", "possible_causes", "inspection_steps", "applicability", "safety_reminder", "citations"],
        "answer_focus": "fault symptom, possible causes, inspection steps, applicability, and safety reminders",
    },
    "parts": {
        "domain_agent": "PartsAgent",
        "required_sections": ["conclusion", "part_numbers", "applicability", "citations", "uncertainty"],
        "answer_focus": "part number evidence, compatible model boundary, and uncertainty when sources are incomplete",
    },
    "visual_inspection": {
        "domain_agent": "VisualInspectionAgent",
        "required_sections": ["visual_observation", "conclusion", "applicability", "citations", "uncertainty"],
        "answer_focus": "visual clues as retrieval hints, confirmed only by document evidence",
    },
    "general": {
        "domain_agent": "GeneralKnowledgeAgent",
        "required_sections": ["conclusion", "citations", "uncertainty"],
        "answer_focus": "source-grounded general answer",
    },
}


class DomainSpecialistAgent:
    name = "DomainSpecialistAgent"

    def run(self, state: AgentState) -> AgentResult:
        route = state.get("route") or {}
        question_type = str(route.get("question_type") or "general")
        strategy = DOMAIN_STRATEGIES.get(question_type, DOMAIN_STRATEGIES["general"])
        domain_context = {
            "question_type": question_type,
            **strategy,
        }
        domain_agent = str(domain_context["domain_agent"])
        return {
            "status": "ok",
            "output": {"domain_context": domain_context},
            "trace": agent_trace(
                agent=domain_agent,
                step="domain_strategy",
                status="ok",
                decision=question_type,
                reason=str(domain_context["answer_focus"]),
                required_sections=domain_context["required_sections"],
            ),
        }
