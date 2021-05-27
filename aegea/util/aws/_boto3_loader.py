from typing import Dict, Any

class Loader:
    cache = dict(resource={}, client={})  # type: Dict[str, Any]
    client_kwargs = dict(default={})  # type: Dict[str, Dict]

    def __init__(self, factory):
        self.factory = factory

    def __getattr__(self, attr):
        if attr == "__name__":
            return "Loader"
        if attr == "__bases__":
            return (object, )
        if attr == "__all__":
            return list(self.cache[self.factory])
        if attr == "__file__":
            return __file__
        if attr == "__path__":
            return []
        if attr == "__loader__":
            return self
        if attr not in self.cache[self.factory]:
            if self.factory == "client" and attr in self.cache["resource"]:
                self.cache["client"][attr] = self.cache["resource"][attr].meta.client
            else:
                import boto3
                factory = getattr(boto3, self.factory)
                self.cache[self.factory][attr] = factory(attr.replace("_", "-"),
                                                         **self.client_kwargs.get(attr, self.client_kwargs["default"]))
        return self.cache[self.factory][attr]
