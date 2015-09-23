from __future__ import absolute_import

import os
import re
import urllib
import urlparse

from .. import idiokit, socket, ssl
from ..dns import host_lookup
from .server import write_headers, read_headers, normalized_headers, get_header_single, get_header_list, get_content_length, _LimitedWriter, _ChunkedWriter, _Buffered, _Limited, _Chunked, ConnectionLost
from . import httpversion


class RequestError(Exception):
    pass


@idiokit.stream
def write_request_line(socket, method, uri, http_version):
    yield socket.sendall("{0} {1} {2}\r\n".format(method, uri, http_version))


@idiokit.stream
def read_status_line(buffered):
    line = yield buffered.read_line()
    if not line:
        raise ConnectionLost()

    match = re.match(r"^([^ ]+) (\d{3}) ([^\r\n]*)\r?\n$", line)
    if not match:
        raise RequestError("could not parse status line")

    http_version_string, code_string, reason = match.groups()
    try:
        http_version = httpversion.HTTPVersion.from_string(http_version_string)
    except ValueError:
        raise RequestError("invalid HTTP version")

    idiokit.stop(http_version, int(code_string), reason)


class ClientResponse(object):
    def __init__(self, http_version, status_code, status_reason, headers, buffered):
        self._http_version = http_version
        self._status_code = status_code
        self._status_reason = status_reason
        self._headers = headers
        self._reader = self._resolve_reader(http_version, headers, buffered)

    @property
    def http_version(self):
        return self._http_version

    @property
    def status_code(self):
        return self._status_code

    @property
    def status_reason(self):
        return self._status_reason

    @property
    def headers(self):
        return self._headers

    def _resolve_reader(self, http_version, headers, buffered):
        if http_version == httpversion.HTTP10:
            return self._resolve_reader_http10(headers, buffered)
        elif http_version == httpversion.HTTP11:
            return self._resolve_reader_http11(headers, buffered)
        raise RequestError("HTTP version {0} not supported".format(http_version))

    def _resolve_reader_http10(self, headers, buffered):
        content_length = get_content_length(headers, None)
        if content_length is None:
            return buffered
        return _Limited(buffered, content_length)

    def _resolve_reader_http11(self, headers, buffered):
        transfer_encoding = get_header_list(headers, "transfer-encoding", None)
        content_length = get_content_length(headers, None)

        if transfer_encoding is not None:
            transfer_encoding = transfer_encoding.lower()

        if transfer_encoding == "chunked":
            return _Chunked(buffered)
        if transfer_encoding in (None, "identity") and content_length is not None:
            return _Limited(buffered, content_length)
        if transfer_encoding in (None, "identity"):
            return buffered
        raise ValueError("either content-length or transfer-encoding: chunked must be used")

    def read(self, amount):
        return self._reader.read(amount)


class ClientRequest(object):
    def __init__(self, method, uri, headers, writer, buffered):
        self._uri = uri
        self._method = method
        self._headers = headers
        self._writer = writer
        self._buffered = buffered

    @property
    def method(self):
        return self._method

    @property
    def uri(self):
        return self._uri

    @property
    def headers(self):
        return self._headers

    def write(self, data):
        return self._writer.write(data)

    @idiokit.stream
    def finish(self):
        yield self._writer.finish()
        http_version, code, reason = yield read_status_line(self._buffered)
        headers = yield read_headers(self._buffered)
        idiokit.stop(ClientResponse(http_version, code, reason, headers, self._buffered))


def _normalize_verify(verify):
    if isinstance(verify, basestring):
        require_cert = True
        ca_certs = verify
    elif verify is True:
        require_cert = True
        ca_certs = None
    elif verify is False:
        require_cert = False
        ca_certs = None
    else:
        raise TypeError("\"verify\" parameter must be a boolean or a string")
    return require_cert, ca_certs


def _normalize_cert(cert):
    if cert is None:
        certfile = None
        keyfile = None
    elif isinstance(cert, basestring):
        certfile = cert
        keyfile = cert
    else:
        certfile, keyfile = cert
    return certfile, keyfile


class _Scheme(object):
    @idiokit.stream
    def connect(self, client, url):
        yield None


class _HTTPScheme(_Scheme):
    default_port = 80

    def connect(self, client, url):
        parsed = urlparse.urlparse(url)

        @idiokit.stream
        def _connect(port):
            family, ip = yield idiokit.next()
            sock = socket.Socket(family)
            yield sock.connect((ip, port), timeout=client.timeout)
            idiokit.stop(sock)

        host = parsed.hostname
        port = self.default_port if parsed.port is None else parsed.port
        return host_lookup(host, client.resolver) | _connect(port)


class _HTTPSScheme(_HTTPScheme):
    default_port = 443

    @idiokit.stream
    def connect(self, client, url):
        parsed = urlparse.urlparse(url)

        require_cert, ca_certs = _normalize_verify(client.verify)
        certfile, keyfile = _normalize_cert(client.cert)

        sock = yield _HTTPScheme.connect(self, client, url)
        sock = yield ssl.wrap_socket(
            sock,
            certfile=certfile,
            keyfile=keyfile,
            require_cert=require_cert,
            ca_certs=ca_certs,
            timeout=client.timeout
        )
        if require_cert:
            cert = yield sock.getpeercert()
            ssl.match_hostname(cert, parsed.hostname)
        idiokit.stop(sock)


class _HTTPUnixScheme(object):
    @idiokit.stream
    def connect(self, client, url):
        parsed = urlparse.urlparse(url)
        socket_path = os.path.join("/", urllib.unquote(parsed.hostname))

        sock = socket.Socket(socket.AF_UNIX)
        yield sock.connect(socket_path, timeout=client.timeout)
        idiokit.stop(sock)


class Client(object):
    def __init__(self, resolver=None, timeout=60.0, verify=True, cert=None):
        _normalize_verify(verify)
        _normalize_cert(cert)

        self._resolver = resolver
        self._verify = verify
        self._cert = cert
        self._timeout = timeout

        self._schemes = {}
        self._set_scheme("http", _HTTPScheme())
        self._set_scheme("https", _HTTPSScheme())

    @property
    def resolver(self):
        return self._resolver

    @property
    def timeout(self):
        return self._timeout

    @property
    def verify(self):
        return self._verify

    @property
    def cert(self):
        return self._cert

    def _set_scheme(self, scheme, handler):
        self._schemes[scheme] = handler

    @idiokit.stream
    def request(self, method, url, headers={}, data=""):
        parsed = urlparse.urlparse(url)

        scheme_handler = self._schemes.get(parsed.scheme, None)
        if scheme_handler is None:
            raise ValueError("unknown URI scheme '{0}'".format(parsed.scheme))

        sock = yield scheme_handler.connect(self, url)
        writer, headers = self._resolve_headers(method, parsed.hostname, headers, data, sock)

        path = urlparse.urlunparse(["", "", "/" if parsed.path == "" else parsed.path, "", parsed.query, ""])
        yield write_request_line(sock, method, path, httpversion.HTTP11)
        yield write_headers(sock, headers)

        request = ClientRequest(method, url, headers, writer, _Buffered(sock))
        yield request.write(data)
        idiokit.stop(request)

    def _resolve_headers(self, method, host, headers, data, socket):
        headers = normalized_headers(headers)
        if headers.get("host", None) is None:
            headers["host"] = host

        connection = get_header_single(headers, "connection", "close")
        if connection.lower() != "close":
            raise ValueError("unknown connection value '{0}'".format(connection))
        headers["connection"] = connection

        transfer_encoding = get_header_list(headers, "transfer-encoding", None)
        content_length = get_content_length(headers, len(data))

        if transfer_encoding is not None:
            if transfer_encoding.lower() not in ("identity", "chunked"):
                raise ValueError("unknown transfer encoding '{0}'".format(transfer_encoding))
            transfer_encoding = transfer_encoding.lower()

        if method == "HEAD":
            if content_length != 0:
                raise ValueError("no content-length != 0 allowed for HEAD requests")
            writer = _LimitedWriter(socket, 0, "no response body allowed for HEAD requests")
            headers["content-length"] = 0
        elif transfer_encoding == "chunked":
            writer = _ChunkedWriter(socket)
        else:
            writer = _LimitedWriter(socket, content_length, "content length set to {0} bytes".format(content_length))
            headers["content-length"] = content_length

        return writer, headers


request = Client().request
