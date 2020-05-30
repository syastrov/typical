import dataclasses
import datetime
import decimal
import enum
import inspect
import ipaddress
import pathlib
import re
import uuid
from collections.abc import (
    Mapping as Mapping_abc,
    Collection as Collection_abc,
    Iterable as Iterable_abc,
)
from operator import methodcaller, attrgetter
from types import MappingProxyType
from typing import (
    Type,
    Callable,
    Collection,
    Union,
    Mapping,
    Any,
    ClassVar,
    cast,
    TYPE_CHECKING,
    TypeVar,
    Iterable,
    MutableMapping,
    Tuple,
    ItemsView,
    KeysView,
    ValuesView,
    Dict,
)

from typic import util, checks, gen, types
from typic.common import DEFAULT_ENCODING
from .common import SerializerT, SerdeConfig, Annotation

if TYPE_CHECKING:  # pragma: nocover
    from .resolver import Resolver


_T = TypeVar("_T")
KT = TypeVar("KT")
VT = TypeVar("VT")
KVPairT = Tuple[KT, VT]


def make_class_serdict(annotation: "Annotation", fields: Mapping[str, SerializerT]):
    name = f"{util.get_name(annotation.resolved_origin)}SerDict"
    bases = (ClassFieldSerDict,)
    getters = annotation.serde.fields_getters
    if annotation.serde.fields_out:
        fout = annotation.serde.fields_out
        getters = {y: getters[x] for x, y in fout.items()}
        fields = {y: fields[x] for x, y in fout.items()}
    ns = dict(
        lazy=False,
        getters=getters,
        fields=fields,
        omit=(*annotation.serde.omit_values,),
        type=annotation.resolved_origin,
    )
    return type(name, bases, ns)


class _Unprocessed:
    def __repr__(self):
        return "<unprocessed>"


Unprocessed = _Unprocessed()


class _Omit:
    def __repr__(self):
        return "<omit>"


Omit = _Omit()


class SerializationValueError(ValueError):
    ...


def _raise_serialization_error(
    inst_tname: str,
    tname: str,
    pre: str,
    sub: bool = True,
    *,
    __err=SerializationValueError,
):
    subt = "a subtype of " if sub else ""
    raise __err(
        f"{pre}type {inst_tname!r} "
        f"is not {subt}type {tname!r}. "
        f"Perhaps this annotation should be "
        f"Union[{inst_tname}, {tname}]?"
    ) from None


def _validate_instance(
    instance,
    t,
    *,
    name=None,
    __isinst=isinstance,
    __err=_raise_serialization_error,
    __getqualname=util.get_qualname,
    __type=type,
):
    if not __isinst(instance, t):
        pre = f"{name}: " if name else ""
        inst_tname = __getqualname(__type(instance))
        tname = __getqualname(t)
        __err(inst_tname, tname, pre)


def _validate_subclass(
    instance,
    t,
    *,
    name=None,
    __issub=util.cached_issubclass,
    __err=_raise_serialization_error,
    __getqualname=util.get_qualname,
    __type=type,
):
    instance_t = __type(instance)
    if not __issub(instance_t, t):
        pre = f"{name}: " if name else ""
        inst_tname = __getqualname(__type(instance))
        tname = __getqualname(t)
        __err(inst_tname, tname, pre)


class ClassFieldSerDict(dict):
    type: Type
    instance: Any
    lazy: bool
    getters: Mapping[str, Callable[[str], Any]]
    fields: Mapping[str, SerializerT]
    omit: Iterable[Any]

    def __init__(
        self, instance: Any, lazy: bool = False, *, __tfname__: util.ReprT = None,
    ):
        self._name = __tfname__
        self.instance = instance
        self.lazy = lazy
        super().__init__(((x, Unprocessed) for x in self.fields))

    def __hash__(self):  # pragma: nocover
        h = getattr(self.instance, "__hash__", None)
        return h() if h else None

    def __iter__(self):
        removals = set()
        remadd = removals.add
        pop = self.pop
        for k in super().__iter__():
            v = self[k]
            if v is Omit:
                remadd(k)
                continue
            yield k
        for rk in removals:
            pop(rk)

    def __getitem__(self, item) -> Union[VT, _Omit]:
        value = super().__getitem__(item)
        if value is Unprocessed:
            raw = self.getters[item](self.instance)
            if raw in self.omit:
                serialized = Omit
            else:
                serialized = self.fields[item](  # type: ignore
                    raw, lazy=self.lazy, name=util.joinedrepr(self._name, item)
                )
            self[item] = serialized
            return serialized
        return value

    def items(self) -> ItemsView[KT, VT]:  # pragma: nocover
        return ItemsView(self)  # type: ignore

    def keys(self) -> KeysView[KT]:  # pragma: nocover
        return KeysView(self)  # type: ignore

    def values(self) -> ValuesView[VT]:  # pragma: nocover
        return ValuesView(self)  # type: ignore


def make_kv_serdict(annotation: "Annotation", kser: SerializerT, vser: SerializerT):
    name = f"{util.get_name(annotation.resolved_origin)}KVSerDict"
    bases = (KVSerDict,)
    ns = dict(
        type=annotation.generic,
        lazy=False,
        kser=staticmethod(kser),
        vser=staticmethod(vser),
        omit=(*annotation.serde.omit_values,),
    )
    return type(name, bases, ns)


class KVSerDict(dict):
    kser: SerializerT
    vser: SerializerT
    lazy: bool
    omit: Iterable[Any]

    def __init__(
        self, seq=None, *, lazy: bool = False, __tfname__: util.ReprT, **kwargs
    ):
        kser = self.kser
        self._name = __tfname__
        self._unprocessed = {
            kser(k): v for k, v in dict(seq, **kwargs).items()  # type: ignore
        }
        super().__init__(((k, Unprocessed) for k in self._unprocessed))
        self.lazy = lazy

    def __iter__(self):
        pop = self.pop
        removals = set()
        remadd = removals.add
        for k in super().__iter__():
            if self[k] is Omit:
                remadd(k)
                continue
            yield k
        for rk in removals:
            pop(rk)

    def __getitem__(self, item) -> Union[VT, _Omit]:
        value = super().__getitem__(item)
        if value in self.omit:
            return Omit
        if value is Unprocessed:
            newv = self.vser(  # type: ignore
                self._unprocessed[item],
                lazy=self.lazy,
                name=util.collectionrepr(self._name, item),
            )
            self[item] = newv
            return newv
        return value

    def items(self) -> ItemsView[KT, VT]:  # pragma: nocover
        return ItemsView(self)  # type: ignore

    def keys(self) -> KeysView[KT]:
        return KeysView(self)  # type: ignore

    def values(self) -> ValuesView[VT]:  # pragma: nocover
        return ValuesView(self)  # type: ignore


def make_serlist(annotation: "Annotation", serializer: SerializerT):
    name = f"{util.get_name(annotation.resolved_origin)}SerList"
    bases = (SerList,)
    ns = dict(
        lazy=False,
        serializer=staticmethod(serializer),
        omit=(*annotation.serde.omit_values,),
    )
    return type(name, bases, ns)


class SerList(list):
    serializer: SerializerT
    omit: Iterable[Any]
    lazy: bool

    def __init__(self, seq=(), *, __tfname__: util.ReprT, lazy: bool = False):
        self.lazy = lazy
        self._name = __tfname__
        ser = self.serializer
        super().__init__(
            (
                ser(x, lazy=lazy, name=util.collectionrepr(self._name, i))  # type: ignore
                for i, x in enumerate(seq)
            )
        )

    def __setitem__(self, key, value):  # pragma: nocover
        super().__setitem__(
            key,
            self.serializer(
                value, lazy=self.lazy, name=util.collectionrepr(self._name, key)
            ),
        )

    def append(self, object: _T) -> None:  # pragma: nocover
        super().append(
            self.serializer(  # type: ignore
                object, lazy=self.lazy, name=util.collectionrepr(self._name, len(self)),
            )
        )


def _iso(o) -> str:
    if isinstance(o, (datetime.datetime, datetime.time)) and not o.tzinfo:
        return f"{o.isoformat()}+00:00"
    return o.isoformat()


_decode = methodcaller("decode", DEFAULT_ENCODING)
_total_secs = methodcaller("total_seconds")
_pattern = attrgetter("pattern")


class SerFactory:
    """A factory for generating high-performance serializers.

    Notes
    -----
    Should not be used directly.
    """

    _DEFINED: Mapping[Type, Callable[[Any], Any]] = {
        ipaddress.IPv4Address: str,
        ipaddress.IPv4Network: str,
        ipaddress.IPv6Address: str,
        ipaddress.IPv6Interface: str,
        ipaddress.IPv6Network: str,
        re.Pattern: _pattern,  # type: ignore
        pathlib.Path: str,
        types.AbsoluteURL: str,
        types.DSN: str,
        types.DirectoryPath: str,
        types.Email: str,
        types.FilePath: str,
        types.HostName: str,
        types.NetworkAddress: str,
        types.RelativeURL: str,
        types.SecretBytes: lambda o: _decode(o.secret),
        types.SecretStr: attrgetter("secret"),
        types.URL: str,
        uuid.UUID: str,
        decimal.Decimal: float,
        bytes: _decode,
        bytearray: _decode,
        datetime.date: _iso,
        datetime.datetime: _iso,
        datetime.time: _iso,
        datetime.timedelta: _total_secs,
    }

    _LISTITER = (
        list,
        tuple,
        set,
        frozenset,
        Collection,
        Collection_abc,
        Iterable,
        Iterable_abc,
    )
    _DICTITER = (dict, Mapping, Mapping_abc, MappingProxyType, types.FrozenDict)
    _PRIMITIVES = (str, int, bool, float, type(None))
    _DYNAMIC = frozenset(
        {Union, Any, inspect.Parameter.empty, dataclasses.MISSING, ClassVar}
    )
    _FNAME = "fname"

    def __init__(self, resolver: "Resolver"):
        self.resolver = resolver
        self._serializer_cache: MutableMapping[str, SerializerT] = {}

    @staticmethod
    def _get_name(annotation: "Annotation") -> str:
        return util.get_defname("serializer", annotation)

    def _check_add_null_check(self, func: gen.Block, annotation: "Annotation"):
        if annotation.optional:
            with func.b("if o is None:") as b:
                b.l(f"{gen.Keyword.RET}")

    def _add_type_check(self, func: gen.Block, annotation: "Annotation"):
        validator = (
            _validate_instance
            if checks.isbuiltinsubtype(annotation.generic)
            else _validate_subclass
        )
        resolved_name = util.get_name(annotation.resolved)
        func.l(f"{self._FNAME} = name or {resolved_name!r}")
        func.l(
            f"_validate(o, t, name={self._FNAME})",
            _validate=validator,
            t=annotation.generic,
        )

    def _build_list_serializer(
        self, func: gen.Block, annotation: "Annotation",
    ):
        # Check for value types
        line = "[*o]"
        ns: Dict[str, Any] = {}
        if annotation.args:
            arg_a: "Annotation" = self.resolver.annotation(
                annotation.args[0], flags=annotation.serde.flags
            )
            arg_ser = self.factory(arg_a)
            arg_ser_name = "arg_ser"

            serlist = make_serlist(annotation, arg_ser)
            serlist_name = serlist.__name__

            ns = {serlist_name: serlist, arg_ser_name: arg_ser}
            line = f"{serlist_name}(o, lazy=lazy, __tfname__={self._FNAME})"

        self._check_add_null_check(func, annotation)
        self._add_type_check(func, annotation)
        func.l(f"{gen.Keyword.RET} {line}", level=None, **ns)

    def _build_key_serializer(
        self, name: str, kser: SerializerT, annotation: "Annotation"
    ) -> SerializerT:
        kser_name = util.get_name(kser)
        # Build the namespace
        ns: Dict[str, Any] = {
            kser_name: kser,
        }
        kvar = "k_"
        with gen.Block(ns) as main:
            with main.f(
                name, main.param(kvar), main.param("lazy", default=False)
            ) as kf:
                k = f"{kser_name}({kvar})"
                # If there are args & field mapping, get the correct field name
                # AND serialize the key.
                if annotation.serde.fields_out:
                    ns["fields_out"] = annotation.serde.fields_out
                    k = f"{kser_name}(fields_out.get({kvar}, {kvar}))"
                # If there are only serializers, get the serialized value
                if annotation.serde.flags.case:
                    ns.update(case=annotation.serde.flags.case.transformer)
                    k = f"case({k})"
                kf.l(f"{gen.Keyword.RET} {k}")
        return main.compile(name=name, ns=ns)

    def _finalize_mapping_serializer(
        self, func: gen.Block, serdict: Type, annotation: "Annotation",
    ):
        serdict_name = serdict.__name__
        self._check_add_null_check(func, annotation)
        self._add_type_check(func, annotation)

        ns: Dict[str, Any] = {serdict_name: serdict}
        func.l(
            f"d = {serdict_name}(o, lazy=lazy, __tfname__={self._FNAME})",
            level=None,
            **ns,
        )
        # Write the line.
        line = "d if lazy else {{**d}}"
        func.l(f"{gen.Keyword.RET} {line}")

    def _build_dict_serializer(self, func: gen.Block, annotation: "Annotation"):
        # Check for args
        kser_: SerializerT
        vser_: SerializerT
        kser_, vser_ = self.resolver.primitive, self.resolver.primitive
        args = util.get_args(annotation.resolved)
        if args:
            kt, vt = args
            ktr: "Annotation" = self.resolver.annotation(
                kt, flags=annotation.serde.flags
            )
            vtr: "Annotation" = self.resolver.annotation(
                vt, flags=annotation.serde.flags
            )
            kser_, vser_ = (self.factory(ktr), self.factory(vtr))
        kser_ = self._build_key_serializer(f"{func.name}_kser", kser_, annotation)
        # Get the names for our important variables

        serdict = make_kv_serdict(annotation, kser_, vser_)

        self._finalize_mapping_serializer(func, serdict, annotation)

    def _build_class_serializer(
        self, func: gen.Block, annotation: "Annotation",
    ):
        # Get the field serializers
        fields_ser = {x: self.factory(y) for x, y in annotation.serde.fields.items()}

        serdict = make_class_serdict(annotation, fields_ser)

        self._finalize_mapping_serializer(func, serdict, annotation)

    def _compile_enum_serializer(self, annotation: "Annotation",) -> SerializerT:
        origin: Type[enum.Enum] = cast(Type[enum.Enum], annotation.resolved_origin)
        ts = {type(x.value) for x in origin}
        # If we can predict a single type the return the serializer for that
        if len(ts) == 1:
            t = ts.pop()
            va = self.resolver.annotation(t, flags=annotation.serde.flags)
            vser = self.factory(va)

            def serializer(
                o: enum.Enum, *, lazy: bool = False, name: util.ReprT = None, _vser=vser
            ):
                return _vser(o.value, lazy=lazy, name=name)

            return serializer
        # Else default to lazy serialization
        return self.resolver.primitive

    def _compile_defined_serializer(
        self, annotation: "Annotation", ser: SerializerT,
    ) -> SerializerT:
        func_name = self._get_name(annotation)
        ser_name = "ser"
        ns = {ser_name: ser}
        with gen.Block(ns) as main:
            with main.f(
                func_name,
                main.param("o"),
                main.param("lazy", default=False),
                main.param("name", default=None),
            ) as func:
                self._check_add_null_check(func, annotation)
                self._add_type_check(func, annotation)
                line = f"{ser_name}(o)"
                func.l(f"{gen.Keyword.RET} {line}")

        serializer: SerializerT = main.compile(name=func_name, ns=ns)
        return serializer

    def _compile_defined_subclass_serializer(
        self, origin: Type, annotation: "Annotation"
    ):
        for t, s in self._DEFINED.items():
            if issubclass(origin, t):
                return self._compile_defined_serializer(annotation, s)
        # pragma: nocover

    def _compile_primitive_subclass_serializer(
        self, origin: Type, annotation: "Annotation"
    ):
        for t in self._PRIMITIVES:
            if issubclass(origin, t):
                return self._compile_defined_serializer(annotation, t)
        # pragma: nocover

    def _compile_serializer(self, annotation: "Annotation") -> SerializerT:
        # Check for an optional and extract the type if possible.
        func_name = self._get_name(annotation)
        # We've been here before...
        if func_name in self._serializer_cache:
            return self._serializer_cache[func_name]

        serializer: SerializerT
        origin = annotation.resolved_origin
        # Lazy shortcut for messy paths (Union, Any, ...)
        if origin in self._DYNAMIC or not annotation.static:
            serializer = self.resolver.primitive
        # Enums are special
        elif checks.isenumtype(annotation.resolved):
            serializer = self._compile_enum_serializer(annotation)
        # Primitives don't require further processing.
        # Just check for nullable and the correct type.
        elif origin in self._PRIMITIVES:
            ns: dict = {}
            with gen.Block(ns) as main:
                with main.f(
                    func_name,
                    main.param("o"),
                    main.param("lazy", default=False),
                    main.param("name", default=None),
                ) as func:
                    self._check_add_null_check(func, annotation)
                    self._add_type_check(func, annotation)
                    func.l(f"{gen.Keyword.RET} o")

            serializer = main.compile(name=func_name, ns=ns)
            self._serializer_cache[func_name] = serializer

        # Defined cases are pre-compiled, but we have to check for optionals.
        elif origin in self._DEFINED:
            serializer = self._compile_defined_serializer(
                annotation, self._DEFINED[origin]
            )
        elif issubclass(origin, (*self._DEFINED,)):
            serializer = self._compile_defined_subclass_serializer(origin, annotation)
        elif issubclass(origin, self._PRIMITIVES):
            serializer = self._compile_primitive_subclass_serializer(origin, annotation)
        else:
            # Build the function namespace
            anno_name = f"{func_name}_anno"
            ns = {anno_name: origin, **annotation.serde.asdict()}
            with gen.Block(ns) as main:
                with main.f(
                    func_name,
                    main.param("o"),
                    main.param("lazy", default=False),
                    main.param("name", default=None),
                ) as func:
                    # Mapping types need special nested processing as well
                    if not checks.istypeddict(origin) and issubclass(
                        origin, self._DICTITER
                    ):
                        self._build_dict_serializer(func, annotation)
                    # Array types need nested processing.
                    elif not checks.istypedtuple(origin) and issubclass(
                        origin, self._LISTITER
                    ):
                        self._build_list_serializer(func, annotation)
                    # Build a serializer for a structured class.
                    else:
                        self._build_class_serializer(func, annotation)
            serializer = main.compile(name=func_name, ns=ns)
            self._serializer_cache[func_name] = serializer
        return serializer

    def factory(self, annotation: "Annotation"):
        annotation.serde = annotation.serde or SerdeConfig()
        return self._compile_serializer(annotation)
