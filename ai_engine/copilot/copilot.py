import re
from collections import Counter

class Copilot:
    """Deterministic, source-aware investigation copilot.

    It answers from the case-scoped index supplied by the service. It never invents
    evidence and explicitly labels analytical interpretations as review signals.
    """
    ID_RE = re.compile(r"\b(?:P\d{3}|P-[A-Z0-9]+|A\d{3}|A-[A-Z0-9]+|V\d{3}|V-[A-Z0-9]+|L\d{3}|L-[A-Z0-9]+|PH\d{3}|PH-[A-Z0-9]+|F\d{3}|FIR-[A-Z0-9]+|ORG\d{2}|ORG-[A-Z0-9]+|E\d{3}|EM-[A-Z0-9]+|PS-[A-Z0-9]+)\b")

    def _source_refs(self, rows, limit=20):
        return sorted({str(x.get("source_ref")) for x in rows if x.get("source_ref")})[:limit]

    def answer(self, query, summary, path=None, centrality=None, communities=None,
               entities=None, relationships=None, events=None, alerts=None, contradictions=None, fir_links=None):
        q=(query or "").strip().lower()
        entities=entities or []; relationships=relationships or []; events=events or []
        alerts=alerts or []; contradictions=contradictions or []; fir_links=fir_links or []; centrality=centrality or {}
        entity_by_id={e.get("external_id"):e for e in entities}

        if path is not None:
            if path.get("path"):
                rels="; ".join(f"{r.get('source')} —[{r.get('relation')}]→ {r.get('target')}" for r in path.get("relationships",[]))
                refs=self._source_refs(path.get("relationships",[])) or ["graph://shortest-path"]
                return {"answer":f"A {path.get('hops',0)}-hop indexed connection was found: {' → '.join(path['path'])}. {rels or 'The connection is based on graph topology only.'}","sources":refs,"path":path,"disclaimer":"Analytical output is an investigative signal, not a finding of guilt. Verify the underlying source records."}
            return {"answer":f"No indexed graph path was found between {path.get('source')} and {path.get('target')}.","sources":["graph://shortest-path"],"disclaimer":"Absence of an indexed path does not establish absence of a real-world relationship."}

        ids=self.ID_RE.findall(query or "")
        if len(ids)>=2 and any(x in q for x in ["connect","connection","linked","related","path","between"]):
            return {"answer":"The requested path was not available from the case-scoped graph query.","sources":["graph://shortest-path"],"disclaimer":"Only indexed, case-scoped relationships are reported."}

        if any(x in q for x in ["summary","overview","how large","how big","what is in this case"]):
            return {"answer":f"The active investigation workspace contains {summary.get('nodes',0)} verified entities, {summary.get('relationships',0)} verified relationships, {summary.get('components',0)} connected network component(s), and {len(events)} indexed timeline event(s).","sources":["graph://summary","event://timeline"],"disclaimer":"Human verification is required for investigative decisions."}

        if any(x in q for x in ["most connected","central","influential","important","bridge"]):
            degree=sorted(centrality.get("degree",{}).items(),key=lambda x:x[1],reverse=True)[:5]
            if not degree: return {"answer":"There are not enough verified relationships to calculate meaningful network-importance candidates yet.","sources":["graph://centrality"],"disclaimer":"Network importance is a prioritization signal, not proof of wrongdoing."}
            rows=[f"{n} ({entity_by_id.get(n,{}).get('name',n)}, {s:.2f})" for n,s in degree]
            return {"answer":"Top network-importance candidates by degree centrality: "+", ".join(rows)+".","sources":["graph://centrality"],"disclaimer":"Network importance prioritizes review; it is not proof of wrongdoing."}

        if "community" in q or "cluster" in q:
            comm=communities or []
            sizes=sorted((len(c) for c in comm),reverse=True)[:8]
            return {"answer":f"The case-scoped graph contains {len(comm)} topology-based community candidate(s). Largest community sizes: {', '.join(map(str,sizes)) or 'none'}.","sources":["graph://communities"],"communities":comm,"disclaimer":"Communities are analytical groupings and require investigator review."}

        if any(x in q for x in ["fir relationship", "fir relation", "firs related", "cross-fir", "cross fir", "related fir"]):
            if not fir_links:
                return {"answer":"No cross-FIR relationship candidates are currently indexed. Verify at least two FIR documents in the same workspace so shared evidence keys can be compared.","sources":["fir://relationships"],"disclaimer":"A shared identifier is an investigative lead, not proof of identity or wrongdoing."}
            top=sorted(fir_links,key=lambda x:x.get("match_score",0),reverse=True)[:5]
            lines=[]
            for x in top:
                shared=[f"{r.get('type')}: {', '.join(r.get('shared',[]))}" for r in x.get("reasons",[])]
                lines.append(f"{x.get('source_fir')} ↔ {x.get('target_fir')} ({float(x.get('match_score',0))*100:.0f}% · {x.get('match_strength')}) — " + "; ".join(shared))
            return {"answer":"Cross-FIR evidence-key comparison: " + " | ".join(lines),"sources":["fir://relationships"],"fir_relationships":top,"disclaimer":"Cross-FIR matches are review leads. They do not establish identity, intent or guilt."}

        if any(x in q for x in ["communication activity", "communication", "calls", "calling"]):
            comm=[e for e in events if str(e.get("event_type","")).upper()=="COMMUNICATION"]
            pairs=Counter((e.get("entity_id"),e.get("related_entity_id")) for e in comm if e.get("related_entity_id"))
            top=pairs.most_common(5)
            detail=", ".join(f"{a} ↔ {b} ({n} event{'s' if n!=1 else ''})" for (a,b),n in top) or "none"
            return {"answer":f"The workspace contains {len(comm)} indexed communication event(s). Most frequent indexed pairs: {detail}.","sources":self._source_refs(comm[:20]) or ["event://communication"],"events":comm[:20],"disclaimer":"Communication records describe indexed activity; they do not establish intent or wrongdoing."}

        if "evidence" in q:
            refs=self._source_refs(events+relationships)
            return {"answer":f"The verified workspace contains {len(refs)} distinct source reference(s) attached to indexed graph/event records. Check the Evidence vault for file integrity and chain-of-custody status.","sources":refs[:20] or ["evidence://workspace"],"disclaimer":"Source presence does not establish evidentiary truth; investigators must verify the underlying material."}

        if "contradict" in q or "conflict" in q:
            return {"answer": (f"The active workspace has {len(contradictions)} contradiction candidate(s)." if contradictions else "No contradiction candidates are currently indexed in the active workspace."), "sources":["analytics://contradictions"], "contradictions":contradictions, "disclaimer":"A contradiction is a data-quality/investigation signal, not a finding of guilt."}

        if any(x in q for x in ["alert","signal","anomal"]):
            top=sorted(alerts,key=lambda x:x.get("confidence",0),reverse=True)[:5]
            detail=", ".join(f"{a.get('code')} ({float(a.get('confidence',0))*100:.0f}%)" for a in top) or "none"
            return {"answer":f"There are {len(alerts)} indexed review signal(s). Highest-confidence signals: {detail}.","sources":self._source_refs([{ "source_ref":r } for a in top for r in a.get("source_refs",[])]) or ["analytics://alerts"],"alerts":top,"disclaimer":"Signals prioritize human review and do not determine guilt."}

        if any(x in q for x in ["transaction","money","financial","payment"]):
            tx=[e for e in events if str(e.get("event_type","")).upper()=="TRANSACTION"]
            total=sum(float(e.get("amount") or 0) for e in tx)
            top=sorted(tx,key=lambda x:float(x.get("amount") or 0),reverse=True)[:5]
            return {"answer":f"The case contains {len(tx)} indexed transaction event(s) totaling approximately ₹{total:,.0f}.","sources":self._source_refs(top) or ["event://transactions"],"transactions":top,"disclaimer":"Financial patterns are analytical signals and require source verification."}

        if "vehicle" in q:
            rows=[r for r in relationships if str(r.get("relation","")).upper() in {"USED","OWNED","REGISTERED_TO","REGISTERED_TO"}]
            return {"answer":f"I found {len(rows)} verified vehicle-related relationship(s) in this workspace.","sources":self._source_refs(rows) or ["graph://vehicles"],"relationships":rows[:20],"disclaimer":"Vehicle associations must be verified against underlying records."}

        if any(x in q for x in ["timeline","recent","changed","event"]):
            ordered=sorted(events,key=lambda x:x.get("event_time") or "")[-10:]
            answer="Recent indexed events: "+("; ".join(f"{e.get('event_time','unknown')} · {e.get('event_type')} · {e.get('entity_id')}" for e in ordered) if ordered else "No dated timeline events are indexed.")
            return {"answer":answer,"sources":self._source_refs(ordered) or ["event://timeline"],"events":ordered,"disclaimer":"Timeline entries are source-indexed events and require verification."}

        if any(x in q for x in ["fir", "case document", "uploaded"]):
            firs=[e for e in entities if str(e.get("type","")).upper()=="CASE"]
            docs=sorted({e.get("attributes",{}).get("document_id") for e in entities if e.get("attributes",{}).get("document_id")})
            return {"answer":f"This workspace currently contains {len(firs)} FIR/case node(s) and {len(docs)} source document reference(s) in the verified graph.","sources":["graph://fir-workspace"],"disclaimer":"FIR records are source material; graph-derived interpretations require human review."}

        return {"answer":"I can answer case-scoped questions about graph paths, network importance, communities, FIR relationships, contradictions, signals, transactions, vehicles and timeline events. Try one of the suggested questions below.","sources":["graph://capabilities"],"disclaimer":"AI assists investigators; it does not accuse or convict."}
