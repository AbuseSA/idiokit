import core
import xmlcore
import threado

PING_NS = "urn:xmpp:ping"
PING_PAYLOAD = xmlcore.Element("ping", xmlns=PING_NS)

class Ping(object):
    def __init__(self, xmpp):
        self.xmpp = xmpp
        self.xmpp.disco.add_feature(PING_NS)
        self.xmpp.core.add_iq_handler(self._ping_iq, "ping", PING_NS)

    def _ping_iq(self, element, payload):
        self.xmpp.core.iq_result(element)
        return True

    @threado.stream
    def ping(inner, self, to):
        try:
            yield inner.sub(self.xmpp.core.iq_get(PING_PAYLOAD, to=to))
        except core.XMPPError, error:
            item = error.type, error.condition
            valid = "cancel", "service-unavailable"
            
            if item != valid:
                raise error
            inner.finish(False)

        inner.finish(True)
