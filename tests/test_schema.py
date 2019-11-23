#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from typing import List, Tuple, Set, Union, Mapping

import pytest

import typic
from typic.schema.field import MultiSchemaField, UndeclaredSchemaField, get_field_type
from typic.compat import Final
from tests import objects


@pytest.mark.parametrize(
    argnames=("obj",), argvalues=[(x,) for x in objects.TYPIC_OBJECTS]
)
def test_typic_objects_schema(obj):
    assert obj.schema() is typic.schema(obj)


@pytest.mark.parametrize(
    argnames=("obj", "expected"),
    argvalues=[
        (str, typic.StrSchemaField()),
        (int, typic.IntSchemaField()),
        (bool, typic.BooleanSchemaField()),
        (float, typic.NumberSchemaField()),
        (list, typic.ArraySchemaField()),
        (set, typic.ArraySchemaField(uniqueItems=True)),
        (frozenset, typic.ArraySchemaField(uniqueItems=True, additionalItems=False)),
        (tuple, typic.ArraySchemaField(additionalItems=False)),
        (List[str], typic.ArraySchemaField(items=(typic.StrSchemaField(),))),
        (
            List[objects.LargeInt],
            typic.ArraySchemaField(
                items=(typic.IntSchemaField(exclusiveMinimum=1000),)
            ),
        ),
        (
            Mapping[str, objects.LargeInt],
            typic.ObjectSchemaField(
                additionalProperties=typic.IntSchemaField(exclusiveMinimum=1000)
            ),
        ),
        (
            objects.FromDict,
            typic.ObjectSchemaField(
                description=objects.FromDict.__doc__,
                title=objects.FromDict.__name__,
                properties=typic.FrozenDict(foo=typic.StrSchemaField()),
                required=(),
                additionalProperties=False,
                definitions=typic.FrozenDict(),
            ),
        ),
        (
            objects.LargeInt,
            typic.IntSchemaField(**objects.LargeInt.__constraints__.for_schema()),
        ),
        (typic.ReadOnly[str], typic.StrSchemaField(readOnly=True)),
        (Final[str], typic.StrSchemaField(readOnly=True)),
        (typic.WriteOnly[str], typic.StrSchemaField(writeOnly=True)),
        (
            Union[str, int],
            typic.MultiSchemaField(
                oneOf=(typic.StrSchemaField(), typic.IntSchemaField())
            ),
        ),
        (
            Mapping[str, int],
            typic.ObjectSchemaField(additionalProperties=typic.IntSchemaField()),
        ),
        (
            Mapping[str, objects.LargeInt],
            typic.ObjectSchemaField(
                additionalProperties=typic.IntSchemaField(exclusiveMinimum=1000)
            ),
        ),
        (
            objects.ShortStrList,
            typic.ArraySchemaField(items=typic.StrSchemaField(maxLength=5)),
        ),
    ],
)
def test_typic_schema(obj, expected):
    assert typic.schema(obj) == expected


@pytest.mark.parametrize(
    argnames=("obj", "expected"),
    argvalues=[
        (str, {"type": "string"}),
        (int, {"type": "integer"}),
        (bool, {"type": "boolean"}),
        (float, {"type": "number"}),
        (list, {"type": "array"}),
        (set, {"type": "array", "uniqueItems": True}),
        (frozenset, {"type": "array", "uniqueItems": True, "additionalItems": False}),
        (tuple, {"type": "array", "additionalItems": False}),
        (List[str], {"type": "array", "items": [{"type": "string"}]}),
        (
            Set[str],
            {"type": "array", "items": [{"type": "string"}], "uniqueItems": True},
        ),
        (
            Tuple[str],
            {"type": "array", "items": [{"type": "string"}], "additionalItems": False},
        ),
        (Tuple[str, ...], {"type": "array", "items": [{"type": "string"}]}),
        (
            objects.FromDict,
            dict(
                description=objects.FromDict.__doc__,
                title=objects.FromDict.__name__,
                properties={"foo": {"type": "string"}},
                required=[],
                additionalProperties=False,
                definitions={},
                type="object",
            ),
        ),
        (
            objects.LargeInt,
            dict(type="integer", **objects.LargeInt.__constraints__.for_schema()),
        ),
    ],
)
def test_typic_schema_primitive(obj, expected):
    assert typic.schema(obj, primitive=True) == expected


@pytest.mark.parametrize(
    argnames=("type", "expected"),
    argvalues=[
        (NotImplemented, UndeclaredSchemaField),
        (None, MultiSchemaField),
        ("string", typic.StrSchemaField),
    ],
)
def test_get_field_type(type, expected):
    assert get_field_type(type) is expected