Your own nodes: one or more .py files here, found when etl2d starts (notes, section 7).
A file starting with _ is skipped. A file that does not load is listed with its error;
the others work.

    from etl2d import node, Input, Output, Param

    @node("Upper", "Text")
    class Upper:
        """Puts a column in capitals."""
        inputs = [Input("features")]
        outputs = [Output("features")]
        params = [Param("column", "str", "NAME")]

        def schema(self, schemas):          # the structure, before any run: unchanged here
            return {"features": schemas["features"]}

        def run(self, inputs):              # the features, one by one
            def gen():
                for f in inputs["features"]:
                    v = f.attributes.get(self.params["column"])
                    if isinstance(v, str):
                        f.attributes[self.params["column"]] = v.upper()
                    yield f
            return {"features": gen()}

A node runs with your rights, as any script: put here only files you trust.
