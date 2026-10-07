from datetime import datetime, timezone

def investigation_report(case, entities, relationships, alerts, tasks, graph_summary=None, centrality=None, events=None, evidence=None, contradictions=None):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "case": case,
        "executive_summary": {"entities_reviewed":len(entities),"relationships_reviewed":len(relationships),"alerts":len(alerts),"tasks":len(tasks),"timeline_events":len(events or []),"evidence_items":len(evidence or []),"contradictions":len(contradictions or [])},
        "graph_summary":graph_summary or {}, "centrality":centrality or {}, "entities":entities, "relationships":relationships, "events":events or [], "alerts":alerts, "tasks":tasks, "evidence":evidence or [], "contradictions":contradictions or [],
        "responsible_ai":"AI outputs are investigative signals. Source-backed facts and AI interpretations must be kept distinct and reviewed by authorized investigators."
    }
