from __future__ import absolute_import

from ._iputils import parse_ip

if False:
    # mypy ignores the "if False" and imports happily
    from typing import Any, Dict, Iterator, Iterable, Tuple, TypeVar  # noqa
    # FrozenSet still missing, see https://github.com/python/mypy/issues/1283
    from typing import FrozenSet  # noqa
    T = TypeVar('T')


def parse_server(server):
    # type: (str) -> Tuple[int, str, int]
    """
    >>> import socket
    >>> parse_server("192.0.2.0") == (socket.AF_INET, "192.0.2.0", 53)
    True
    >>> parse_server("2001:DB8::") == (socket.AF_INET6, "2001:db8::", 53)
    True
    """

    family, ip = parse_ip(server)
    return family, ip, 53


def read_resolv_conf(line_iterator):
    # type: (Iterator[str]) -> Iterator[Tuple[str, str]]
    for line in line_iterator:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        pieces = line.split(None, 1)
        if len(pieces) < 2:
            continue

        key, value = pieces
        yield key.lower(), value


def read_hosts(line_iterator):
    # type: (Iterator[str]) -> Iterator[Tuple[str, List[str]]]
    for line in line_iterator:
        comment_start = line.find("#")
        if comment_start >= 0:
            line = line[:comment_start]

        line = line.strip()
        if not line:
            continue

        pieces = line.split()
        if len(pieces) < 2:
            continue

        try:
            _, ip = parse_ip(pieces[0])
        except ValueError:
            continue
        names = set(pieces[1:])
        yield ip, [name.lower() for name in names]


def uniques(values):
    seen_values = set()

    for value in values:
        if value in seen_values:
            continue
        seen_values.add(value)
        yield value


class Hosts(object):
    @classmethod
    def from_lines(cls, line_iterator):
        ips = {}  # type: Dict[str, Set[str]]
        for ip, names in read_hosts(line_iterator):
            ips.setdefault(ip, set()).update(names)
        return cls(ips)

    def __init__(self, ips):
        self._ips = {}  # type: Dict[str, FrozenSet[str]]
        self._names = {}  # type: Dict[str, Set[str]]

        for ip, names in ips.iteritems():
            self._ips[ip] = frozenset(names)

            for name in names:
                self._names.setdefault(name, set()).add(ip)

    def ip_to_names(self, ip):
        # type: (str) -> Iterator[str]
        _, ip = parse_ip(ip)
        return iter(self._ips.get(ip, frozenset()))

    def name_to_ips(self, name):
        # type: (str) -> Iterator[str]
        return iter(self._names.get(name.lower(), set()))

    @property
    def ips(self):
        # type: () -> Tuple[str, ...]
        return tuple(self._ips)

    @property
    def names(self):
        # type: () -> Tuple[str, ...]
        return tuple(self._names)


class ResolvConf(object):
    @classmethod
    def from_lines(cls, line_iterator):
        servers = []
        for key, value in read_resolv_conf(line_iterator):
            if key != "nameserver":
                continue

            try:
                _, ip, port = parse_server(value)
            except ValueError:
                pass
            else:
                servers.append((ip, port))

        return cls(servers)

    def __init__(self, servers):
        self._servers = tuple(uniques(servers))

    @property
    def servers(self):
        # type: () -> Tuple[str]
        return self._servers


class _Loader(object):
    def __init__(self, type, path):
        self._path = path
        self._type = type

        self._instance = None  # type: Iterator[str]

    def load(self, force_reload=False):
        # type: (bool) -> Iterator[str]
        if self._instance is None:
            opened = None
            try:
                opened = open(self._path, "rb")
            except IOError:
                self._instance = self._type.from_lines([])
            else:
                self._instance = self._type.from_lines(opened)
            finally:
                if opened is not None:
                    opened.close()
        return self._instance


def hosts(path="/etc/hosts"):
    return _Loader(Hosts, path)


def resolv_conf(path="/etc/resolv.conf"):
    return _Loader(ResolvConf, path)
