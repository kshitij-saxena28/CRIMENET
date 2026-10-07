class Neo4jAdapter:
    def __init__(self, uri="", user="", password=""):
        self.enabled = bool(uri and user and password)
        self.uri, self.user, self.password = uri, user, password

    def status(self):
        return {"enabled": self.enabled, "backend": "neo4j" if self.enabled else "networkx-demo", "mode": "optional"}
