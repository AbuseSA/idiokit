import idiokit
import unittest
import tempfile
from socket import AF_INET, AF_INET6

from .. import _hostlookup
from .. import _dns


class HostLookupTests(unittest.TestCase):
    _hosts = None

    def setUp(self):
        self._hosts = tempfile.NamedTemporaryFile()
        self._hosts.writelines([
            "198.51.100.126 ipv4.idiokit.example\n",
            "2001:DB8::cafe ipv6.idiokit.example\n"
        ])
        self._hosts.flush()

    def tearDown(self):
        if self._hosts:
            self._hosts.close()

    def _host_lookup(self, lookup):
        hl = _hostlookup.HostLookup(hosts_file=self._hosts.name)
        return hl.host_lookup(lookup)

    def test_ipv4_host_lookup_with_ip(self):
        self.assertEqual(
            [(AF_INET, '198.51.100.126')],
            idiokit.main_loop(self._host_lookup('198.51.100.126'))
        )

    def test_ipv4_host_lookup_with_name(self):
        self.assertEqual(
            [(AF_INET, '198.51.100.126')],
            idiokit.main_loop(self._host_lookup('ipv4.idiokit.example'))
        )

    def test_ipv6_host_lookup_with_ip(self):
        self.assertEqual(
            [(AF_INET6, '2001:db8::cafe')],
            idiokit.main_loop(self._host_lookup('2001:DB8::cafe'))
        )

    def test_ipv6_host_lookup_with_name(self):
        self.assertEqual(
            [(AF_INET6, '2001:db8::cafe')],
            idiokit.main_loop(self._host_lookup('ipv6.idiokit.example'))
        )

    def test_ipv6_host_lookup_with_unknown_name(self):
        @idiokit.stream
        def main():
            try:
                hl = _hostlookup.HostLookup(hosts_file=self._hosts.name)
                yield hl.host_lookup('notfound.idiokit.example')
            except _dns.ResponseError:
                idiokit.stop()
            else:
                self.fail("Hostname should not be found")
                idiokit.stop()

        idiokit.main_loop(main())
