"""Nodes found in folders at start, as ComfyUI's custom nodes (notes §8)."""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Input:
    name: str


@dataclass(frozen=True)
class Output:
    name: str


@dataclass(frozen=True)
class Param:
    """A parameter; its type decides its widget in the window (§8).

    Types: "str", "int", "float", "bool", "path" (resolved from the workflow's
    folder), "bbox" (four numbers), "crs", "code" (Python), "choice" (one of
    `choices`); lists: "rows" (dictionaries with the keys in `choices`),
    "columns" (one row per input column: keep, rename; the Attributes node),
    "names" (some of the input's column names).
    """

    name: str
    type: str = "str"
    default: Any = None
    choices: tuple = ()
    help: str = ""


@dataclass
class NodeSpec:
    name: str
    category: str
    cls: type
    inputs: list[Input]
    outputs: list[Output]
    params: list[Param]
    file: str = ""
    help: str = ""

    @property
    def is_sink(self) -> bool:
        """No output: it ends a branch (a writer); the runner pulls through it."""
        return not self.outputs

    def input_names(self, params: dict) -> list[str]:
        """Its inputs for these parameters: `inputs_for(params)` when the class has one
        (the MapWriter: one per layer, §7), else the fixed list."""
        fn = getattr(self.cls, "inputs_for", None)
        if fn is not None:
            try:
                return [str(n) for n in fn(params)]
            except Exception:
                pass
        return [i.name for i in self.inputs]

    def output_names(self, params: dict) -> list[str]:
        """Its outputs for these parameters: `outputs_for(params)` when the class has one
        (the Filter: one per rule, then 'else', §7), else the fixed list."""
        fn = getattr(self.cls, "outputs_for", None)
        if fn is not None:
            try:
                return [str(n) for n in fn(params)]
            except Exception:   # parameters it cannot read yet: its fixed ones; schema() will say why
                pass
        return [o.name for o in self.outputs]


def node(name: str, category: str = "Other", help: str = ""):
    """Marks a class as a node. Its `inputs`, `outputs` and `params` are class attributes."""

    def mark(cls):
        cls._etl2d_spec = NodeSpec(name, category, cls, list(getattr(cls, "inputs", [])),
                                   list(getattr(cls, "outputs", [])), list(getattr(cls, "params", [])),
                                   help=help or (cls.__doc__ or "").strip())
        return cls

    return mark


@dataclass
class Registry:
    """Every node type found, by name; and the files that did not load, with why."""

    specs: dict[str, NodeSpec] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def load_folder(self, folder: Path) -> None:
        """Imports every .py file of `folder` (not those starting with _) and keeps its nodes.

        A file that fails is listed in `errors` with its traceback's last line;
        the others load. Called again, it reads the files again ("Reload").
        """
        folder = Path(folder)
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"etl2d_nodes_{folder.name}_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception as e:   # a broken custom node must not stop the others
                last = traceback.format_exception_only(type(e), e)[-1].strip()
                self.errors.append(f"{path.name}: {last}")
                sys.modules.pop(module_name, None)
                continue
            for value in vars(module).values():
                spec_ = getattr(value, "_etl2d_spec", None)
                if isinstance(value, type) and spec_ is not None and value.__module__ == module_name:
                    spec_.file = str(path)
                    if spec_.name in self.specs and self.specs[spec_.name].file != spec_.file:
                        self.errors.append(f"{path.name}: node '{spec_.name}' already defined in "
                                           f"{Path(self.specs[spec_.name].file).name}; this one is used")
                    self.specs[spec_.name] = spec_

    def find(self, name: str) -> NodeSpec | None:
        return self.specs.get(name)


def default_registry(extra: list[Path] | None = None) -> Registry:
    """The built-in nodes (nodes/), then the user's (custom_nodes/), then any extra folder."""
    root = Path(__file__).resolve().parent.parent
    reg = Registry()
    for folder in [root / "nodes", root / "custom_nodes", *(extra or [])]:
        reg.load_folder(folder)
    return reg
