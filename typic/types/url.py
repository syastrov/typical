#!/usr/bin/env python
# -*- coding: UTF-8 -*-
import dataclasses
import re
from collections import defaultdict
from types import MappingProxyType
from typing import Dict, List, ClassVar, Pattern, Match, Mapping, Set, Optional
from urllib import parse

from typic.util import cached_property, slotted
from .secret import SecretStr

__all__ = (
    "NetAddrInfo",
    "NetworkAddress",
    "NetworkAddressValueError",
    "AbsoluteURL",
    "AbsoluteURLValueError",
    "HostName",
    "HostNameValueError",
    "RelativeURL",
    "RelativeURLValueError",
    "URL",
)


# By no means an exhaustive list, but a decent chunk of use-cases
DEFAULT_PORTS = defaultdict(
    set,
    {
        "http": {80},
        "https": {443},
        "ws": {80},
        "wss": {443},
        "smtp": {25},
        "ftp": {20, 21},
        "telnet": {23},
        "imap": {143},
        "rdp": {3389},
        "ssh": {25},
        "dns": {53},
        "dhcp": {67, 68},
        "pop3": {110},
        "mysql": {3306},
        "vertica": {5434},
        "postgresql": {5432},
    },
)
NET_ADDR_PATTERN = re.compile(
    r"""
    ^
    (
        # Scheme
        ((?P<scheme>(?:[a-z0-9\.\-\+]*))://)?
        # Auth
        (?P<auth>(?:(?P<username>[^:@]+?)[:@](?P<password>[^:@]*?)[:@]))?
        # Host
        (?P<host>(?:
            # Domain
            (?P<domain>
                (?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+
                (?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)
            )
            # Localhost
            |(?P<localhost>localhost)
            |(?P<dotless>(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.?))
            # IPV4
            |(?P<ipv4>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})
            # IPV6
            |(?P<ipv6>\[[A-F0-9]*:[A-F0-9:]+\])
        ))?
        # Port
        (:(?P<port>(?:\d+)))?
    )?
    # Path, Q-string & fragment
    (?P<relative>(?:/?|[/?#]\S+))
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)
PRIVATE_HOSTS = {"localhost", "127.0.0.1"}
INTERNAL_HOSTS = PRIVATE_HOSTS | {"0.0.0.0"}


INTERNAL_IP_PATTERN = re.compile(
    r"""
    ^
    # IPv4
    (127\.)|
    (192\.168\.)|
    (10\.)|(172\.1[6-9]\.)|
    (172\.2[0-9]\.)|(172\.3[0-1]\.)|
    # IPv6
    (::1)|([F][CD])
    $
    """,
    re.I | re.VERBOSE,
)


class NetworkAddressValueError(ValueError):
    """A generic error indicating the value is not a valid network address."""


@slotted
@dataclasses.dataclass(frozen=True)
class NetAddrInfo:
    """Detailed information about a network address.

    Can be called directly, generated by casting a :py:class:`str` as
    :py:class:`NetworkAddress`, or created with :py:meth:`NetAddrInfo.from_str`
    """

    scheme: str
    """The net-address scheme, e.g., `http`, `tcp`, `ssh`, etc."""
    auth: str
    """The user auth info."""
    password: SecretStr
    """The user's password."""
    host: str
    """The host for this addres, e.g. `0.0.0.0`, `foobar.net`."""
    port: int
    """The port for this net-address"""
    path: str
    """The URI path."""
    qs: str
    """The query-string, unparsed, e.g. `?id=1&name=foo`"""
    params: str
    """The url parameters, unparsed, e.g. `id=2;foo=bar`"""
    fragment: str
    """The uri fragment, e.g. `#some-page-anchor`"""
    is_ip: bool = False

    PATTERN: ClassVar[Pattern] = NET_ADDR_PATTERN
    DEFAULT_PORTS: ClassVar[Dict] = DEFAULT_PORTS
    PRIVATE_HOSTS: ClassVar[Set[str]] = PRIVATE_HOSTS
    INTERNAL_HOSTS: ClassVar[Set[str]] = INTERNAL_HOSTS

    @classmethod
    def from_str(cls, value) -> "NetAddrInfo":
        """Parse a string, validate, and return an instance of :py:class:`NetAddrInfo`."""
        match: Optional[Match] = cls.PATTERN.match(value)
        if not match or not value:
            raise NetworkAddressValueError(f"{value!r} is not a valid network address.")
        scheme, host = match["scheme"] or "", match["host"] or ""
        if scheme and not host:
            raise NetworkAddressValueError(f"{value!r} is not a valid network address.")
        # why re-invent the wheel here? this is fast and correct.
        parsed: parse.ParseResult = parse.urlparse(match["relative"] or "")
        # get/set the port
        port = int(match["port"] or 0)
        if port == 0 and cls.DEFAULT_PORTS[scheme]:
            port = cls.DEFAULT_PORTS[scheme].copy().pop()

        return cls(
            scheme=scheme,
            auth=match["auth"] or "",
            password=SecretStr(match["password"] or ""),
            host=host,
            port=port,
            path=parsed.path,
            qs=parsed.query,
            params=parsed.params,
            fragment=parsed.fragment,
            is_ip=bool(match["ipv4"] or match["ipv6"]),
        )

    @cached_property
    def base(self) -> str:
        """The 'base' of the URL, including scheme, auth, and host."""
        base = f"{self.scheme}://" if self.scheme else ""
        port = f":{self.port}" if self.port and not self.is_default_port else ""
        return f"{base}{self.auth}{self.host}{port}"

    @cached_property
    def relative(self):
        """The 'relative' portion of the URL: path, params, query, and fragment."""
        params = f";{self.params}" if self.params else ""
        qs = f"?{self.qs}" if self.qs else ""
        fragment = f"#{self.fragment}" if self.fragment else ""
        return f"{self.path}{params}{qs}{fragment}"

    @cached_property
    def address(self) -> str:
        """The fully-qualified network address.

        If this instance was generated from a string, it will match."""
        return f"{self.base}{self.relative}"

    @cached_property
    def address_encoded(self) -> str:
        """The fully-qualified network address, encoded."""
        return parse.quote(self.address)  # type: ignore

    @cached_property
    def query(self) -> Mapping[str, List[str]]:
        """The query-string, parsed into a mapping of key -> [values, ...]."""
        return MappingProxyType(parse.parse_qs(self.qs) if self.qs else {})

    @cached_property
    def parameters(self) -> Mapping[str, List[str]]:
        """The params, parsed into a mapping of key -> [values, ...]."""
        return MappingProxyType(parse.parse_qs(self.params) if self.params else {})

    @cached_property
    def is_default_port(self) -> bool:
        """Whether address is using the default port assigned to the given scheme."""
        defaults = DEFAULT_PORTS[self.scheme] | {0}
        return self.port in defaults

    @cached_property
    def is_relative(self) -> bool:
        """Whether address is 'relative' (i.e., whether a scheme is provided)."""
        return not self.scheme

    @cached_property
    def is_absolute(self) -> bool:
        """The opposite of `is_relative`."""
        return not self.is_relative

    @cached_property
    def is_private(self) -> bool:
        """Whether or not the URL is using a 'private' host, i.e., 'localhost'."""
        return self.host in PRIVATE_HOSTS

    @cached_property
    def is_internal(self) -> bool:
        """Whether the host provided is an 'internal' host.

        This may or may not be private, hence the distinction."""
        return bool(
            self.host
            and self.host in INTERNAL_HOSTS
            or (self.is_ip and INTERNAL_IP_PATTERN.match(self.host))
        )


# Deepcopy is broken for frozen dataclasses with slots.
# https://github.com/python/cpython/pull/17254
# NetAddrInfo.__slots__ = tuple(
#     _.name for _ in dataclasses.fields(NetAddrInfo)
# )


class NetworkAddress(str):
    """A generic, immutable network address string.

    Detailed information about the network address string can be looked up via
    :py:attr:`NetworkAddress.info`.

    This object is the base object for network-related objects.
    :py:class:`URL` has a much richer interface.

    Examples
    --------

    >>> import typic
    >>> net_addr = typic.NetworkAddress("http://foo.bar/bazz;foo=bar?buzz=1#loc")
    >>> net_addr.info.is_absolute
    True
    >>> net_addr.info.host
    'foo.bar'
    >>> net_addr.info.scheme
    'http'
    >>> net_addr.info.address_encoded
    'http%3A//foo.bar/bazz%3Bfoo%3Dbar%3Fbuzz%3D1%23loc'
    >>> net_addr.info.query
    mappingproxy({'buzz': ['1']})
    >>> net_addr.info.parameters
    mappingproxy({'foo': ['bar']})
    >>> net_addr.info.fragment
    'loc'
    >>> domain = typic.URL("foo.bar")
    >>> domain.info.is_relative
    True
    >>> domain.info.host
    'foo.bar'
    >>> net_addr
    'http://foo.bar/bazz;foo=bar?buzz=1#loc'
    >>> import json
    >>> json.dumps([net_addr])
    '["http://foo.bar/bazz;foo=bar?buzz=1#loc"]'

    See Also
    --------
    :py:class:`NetAddrInfo`
    :py:class:`URL`

    Notes
    -----
    This object inherits directly from :py:class:`str` and so is natively
    JSON-serializable.
    """

    def __new__(cls, *args, **kwargs):
        v = super().__new__(cls, *args, **kwargs)
        # Initialize the info so we get validation immediately.
        v.info
        return v

    def __setattr__(self, key, value):
        raise AttributeError(
            f"attempting to set attribute on immutable type {type(self)}"
        )

    def __delattr__(self, key):
        raise AttributeError(
            f"attempting to delete attribute on immutable type {type(self)}"
        )

    @cached_property
    def info(self) -> NetAddrInfo:
        return NetAddrInfo.from_str(self)


class URLValueError(NetworkAddressValueError):
    """Generic error for an invalid value passed to URL."""

    pass


class URL(NetworkAddress):
    """A string which parses the value provided as if it were a URL.

    Detailed information about the url string can be looked up via :py:attr:`URL.info`.

    Examples
    --------

    >>> import typic
    >>> url = typic.URL("http://foo.bar/bazz")
    >>> more = url / 'foo' / 'bar'
    >>> more
    'http://foo.bar/bazz/foo/bar'
    >>> typic.URL(url.info.base) / 'other'
    'http://foo.bar/other'

    See Also
    --------
    :py:class:`NetworkAddress`
    :py:class:`NetAddrInfo`

    Notes
    -----
    This object inherits directly from :py:class:`NetworkAddress` and so is natively
    JSON-serializable.
    """

    def join(self, other) -> "URL":
        """Join another URL with this one.

        This works roughly like :py:meth:`pathlib.Path.joinpath`.

        Unlike :py:func:`urllib.parse.urljoin`, this method allows the user to build
        onto existing paths.
        """
        cls = type(self)
        # best guess at the path
        other_info: NetAddrInfo = cls(other).info  # type: ignore
        self_info: NetAddrInfo = self.info  # type: ignore
        other = (other_info.path or parse.urlparse(other).path or "").lstrip("/")
        other = f"{self_info.path.rstrip('/') or ''}/{other}"
        return cls(parse.urljoin(self_info.base, other))  # type: ignore

    def __truediv__(self, other) -> "URL":
        """Overloading some operators to make it easier. Uses `:py:meth:`URL.join`."""
        return self.join(other)

    def __rtruediv__(self, other) -> "URL":
        return URL(other) / self


class AbsoluteURLValueError(URLValueError):
    pass


class AbsoluteURL(URL):
    """An absolute URL.

    See Also
    --------
    :py:class:`URL`
    """

    def __new__(cls, *args, **kwargs):
        v = super().__new__(cls, *args, **kwargs)
        if v.info.is_relative:
            raise AbsoluteURLValueError(f"<{v!r}> is not an absolute URL.") from None
        return v


class RelativeURLValueError(URLValueError):
    pass


class RelativeURL(URL):
    """A relative URL.

    See Also
    --------
    :py:class:`URL`
    """

    def __new__(cls, *args, **kwargs):
        v = super().__new__(cls, *args, **kwargs)
        if v.info.is_absolute:
            raise RelativeURLValueError(f"<{v!r}> is not a relative URL.") from None
        return v


class HostNameValueError(NetworkAddressValueError):
    pass


class HostName(NetworkAddress):
    """A network address referencing only a host-name (e.g. foo.bar.com).

    See Also
    --------
    :py:class:`NetworkAddress`
    :py:class:`NetAddrInfo`

    Notes
    -----
    This object inherits directly from :py:class:`NetworkAddress` and, so is natively
    JSON-serializable.
    """

    def __new__(cls, *args, **kwargs):
        v = super().__new__(cls, *args, **kwargs)
        if not v.info.host or any((v.info.scheme, v.info.auth, v.info.relative)):
            raise HostNameValueError(f"<{v!r}> is not a hostname.") from None
        return v
