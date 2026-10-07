import logging
from itertools import islice

import networkx as nx

log = logging.getLogger("dcn.graph")


class GraphEngine:
    """Deterministic, explainable in-memory investigation graph."""
    def __init__(self):
        self.g = nx.MultiDiGraph()

    def clear(self):
        self.g.clear()

    def add_entity(self, i, t="Entity", name=None, **attrs):
        self.g.add_node(i, type=t, name=name or i, **attrs)

    def add_relationship(self, s, t, r, confidence=1.0, source_ref="", **attrs):
        self.g.add_edge(s, t, relation=r, confidence=float(confidence or 0), source_ref=source_ref or "", **attrs)

    def summary(self):
        ug = self.g.to_undirected()
        return {
            "nodes": self.g.number_of_nodes(),
            "relationships": self.g.number_of_edges(),
            "components": nx.number_connected_components(ug) if self.g.number_of_nodes() else 0,
        }

    def _simple(self):
        g = nx.Graph()
        for n, data in self.g.nodes(data=True):
            g.add_node(n, **data)
        for s, t, data in self.g.edges(data=True):
            conf = float(data.get("confidence") or 0)
            cost = 1.0 / max(conf, 0.05)
            if g.has_edge(s, t):
                # Keep the strongest evidence edge.
                if conf > g[s][t].get("confidence", 0):
                    g[s][t].update(data)
            else:
                g.add_edge(s, t, **data, cost=cost)
        return g

    def path(self, s, t, weighted=False):
        g = self._simple()
        try:
            if weighted:
                return nx.shortest_path(g, s, t, weight="cost")
            return nx.shortest_path(g, s, t)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def path_details(self, s, t):
        path = self.path(s, t)
        relationships = []
        for a, b in zip(path, path[1:]):
            data = self.g.get_edge_data(a, b) or self.g.get_edge_data(b, a) or {}
            if data:
                edge = max(data.values(), key=lambda d: d.get("confidence", 0))
                relationships.append({"source": a, "target": b, **edge})
        return {"source": s, "target": t, "path": path, "hops": max(len(path) - 1, 0), "relationships": relationships}

    def centrality(self):
        g = self._simple()
        if not g:
            return {"degree": {}, "betweenness": {}, "pagerank": {}, "eigenvector": {}, "closeness": {}}
        try:
            eigen = nx.eigenvector_centrality_numpy(g)
        except (nx.NetworkXException, ArithmeticError, ValueError):
            log.warning("Eigenvector centrality did not converge; reporting zeros")
            eigen = {n: 0.0 for n in g.nodes}
        # Exact betweenness is O(V*E); sample pivots on large graphs.
        k = 500 if g.number_of_nodes() > 1000 else None
        return {
            "degree": nx.degree_centrality(g),
            "betweenness": nx.betweenness_centrality(g, k=k, normalized=True, seed=42),
            "pagerank": nx.pagerank(g),
            "eigenvector": eigen,
            "closeness": nx.closeness_centrality(g),
        }

    def communities(self):
        if not self.g:
            return []
        g = self._simple()
        try:
            communities = nx.community.louvain_communities(g, seed=42)
        except (nx.NetworkXException, ValueError):
            log.warning("Louvain failed; falling back to greedy modularity")
            communities = nx.community.greedy_modularity_communities(g)
        return [sorted(c) for c in communities]

    def subgraph(self, center, hops=2, types=None, relation=None):
        if center not in self.g:
            return []
        hops = max(0, min(int(hops), 5))
        nodes = {center}
        frontier = {center}
        for _ in range(hops):
            nxt = set()
            for n in frontier:
                nxt.update(self.g.neighbors(n)); nxt.update(self.g.predecessors(n))
            nxt -= nodes
            nodes.update(nxt)
            frontier = nxt
        if types:
            type_set = {x.upper() for x in types}
            nodes = {n for n in nodes if str(self.g.nodes[n].get("type", "")).upper() in type_set or n == center}
        if relation:
            relation = relation.upper()
            filtered = {center}
            for a, b, d in self.g.edges(data=True):
                if a in nodes and b in nodes and str(d.get("relation", "")).upper() == relation:
                    filtered.update([a, b])
            nodes = filtered
        return sorted(nodes)

    def hidden_connections(self, source, target, max_hops=4):
        g = self._simple()
        max_hops = max(1, min(int(max_hops), 6))
        try:
            # Enumeration is exponential on dense graphs: hard-cap the number of paths inspected.
            paths = list(islice(nx.all_simple_paths(g, source, target, cutoff=max_hops), 500))
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            return []
        ranked = []
        for p in paths:
            if len(p) < 3:
                continue
            detail = {"source": source, "target": target, "path": p, "hops": len(p) - 1, "relationships": []}
            for a, b in zip(p, p[1:]):
                data = self.g.get_edge_data(a, b) or self.g.get_edge_data(b, a) or {}
                if data:
                    edge = max(data.values(), key=lambda d: d.get("confidence", 0))
                    detail["relationships"].append({"source": a, "target": b, **edge})
            confidence = sum(float(r.get("confidence") or 0) for r in detail["relationships"])
            detail["path_confidence"] = round(confidence / max(len(detail["relationships"]), 1), 4)
            ranked.append(detail)
        return sorted(ranked, key=lambda x: (x["hops"], -x["path_confidence"], tuple(x["path"])))[:10]

    def nodes(self):
        return [{"id": n, **d} for n, d in self.g.nodes(data=True)]

    def edges(self):
        return [{"source": s, "target": t, **d} for s, t, d in self.g.edges(data=True)]
