def explain_alert(title, factors, confidence, entity_id="", source_refs=None, model_version="anomaly-v1"):
    return {
        "title": title,
        "entity_id": entity_id,
        "confidence": round(float(confidence), 3),
        "factors": factors,
        "source_refs": source_refs or [],
        "model_version": model_version,
        "disclaimer": "Investigative signal only; requires authorized human verification."
    }
