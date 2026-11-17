"""The Python caller: a function called for each feature (notes §7)."""

from __future__ import annotations

from etl2d import Feature, Field, Input, Output, Param, SchemaError, node
from etl2d.needs import Needs
from etl2d.schema import FIELD_TYPES

EXAMPLE = '''def process(feature):
    # feature.attributes: a dict; feature.geometry: an ogr.Geometry
    return feature   # None: dropped; a list: several features
'''


@node("PythonCaller", "Python")
class PythonCaller:
    """process(feature) for each feature: returns it, None (dropped) or a list (several)."""

    inputs = [Input("features")]
    outputs = [Output("features")]
    params = [Param("code", "code", EXAMPLE),
              Param("new_columns", "rows", [], choices=("name", "type", "width"),
                    help="the columns the code adds (their names and types)"),
              Param("reads", "names", [], help="the columns the code reads; none ticked: all are read (§5)")]

    def _function(self):
        namespace = {"Feature": Feature}
        try:
            exec(compile(self.params["code"] or "", "<PythonCaller>", "exec"), namespace)
        except SyntaxError as e:
            raise SchemaError(f"line {e.lineno}: {e.msg}")
        fn = namespace.get("process")
        if not callable(fn):
            raise SchemaError("the code defines no function process(feature)")
        return fn

    def schema(self, schemas):
        self._function()
        s = schemas["features"]
        fields = list(s.fields)
        for c in self.params["new_columns"] or []:
            if c.get("type", "String") not in FIELD_TYPES:
                raise SchemaError(f"column {c.get('name')}: unknown type {c.get('type')}")
            if s.field(c["name"]) is None:
                fields.append(Field(c["name"], c.get("type", "String"), int(c.get("width", 0))))
        return {"features": s.with_fields(fields).with_count(None)}

    def needs(self, wanted):
        """§5: the code is not read to guess what it uses. Its 'reads' say it; without them, everything."""
        w = wanted.get("features", Needs.everything())
        reads = self.params["reads"] or []
        if not reads or w.columns is None:
            return {"features": Needs(None, None)}   # its own box is unknown too: it may move features
        added = {c["name"] for c in self.params["new_columns"] or []}
        return {"features": Needs(None, frozenset(reads) | (w.columns - added))}

    def run(self, inputs):
        fn = self._function()

        def gen():
            for f in inputs["features"]:
                try:
                    out = fn(f)
                except Exception as e:
                    # the feature that caused it: usually a value nobody expected
                    raise RuntimeError(f"{type(e).__name__}: {e} — on {f.describe()}") from e
                if out is None:
                    continue
                if isinstance(out, Feature):
                    yield out
                else:
                    yield from out

        return {"features": gen()}
