import collections
import dbus
import dbus.exceptions
import dbus.service
import gi.repository
import io
import json
import logging
import os
import pykka
import struct
import threading

from mopidy.core import Core
from mopidy.core import CoreListener
from mopidy.http.handlers import make_jsonrpc_wrapper
from mopidy.models.serialize import ModelJSONEncoder

log = logging.getLogger(__name__)


class BtRpcServer(pykka.ThreadingActor, CoreListener):
    def __init__(self, config, core):
        pykka.ThreadingActor.__init__(self)

        self.config = config
        self.core = core  # type: Core

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._mainloop = gi.repository.GObject.MainLoop()

        self._spp = SerialPort(1)
        self._server = BluetoothServer(
            core,
            dbus.SystemBus(),
            self._spp.profile_path,
        )
        self._thread = threading.Thread(
            name='bluetooth server',
            target=self.startup,
        )

    def startup(self):
        if not self._spp.register():
            return

        self._mainloop.run()

    def shutdown(self):
        self._mainloop.quit()
        self._spp.unregister()

    def on_start(self):
        self._thread.start()

    def on_stop(self):
        self.shutdown()
        self._thread.join(1)

    def on_event(self, name, **data):
        event = data
        event['event'] = name
        message = json.dumps(event, cls=ModelJSONEncoder)

        self._server.broadcast(message)


class SerialPort(object):
    profile_path = "/org/bluez/mopidy"

    def __init__(self, channel=1):
        self.bus = dbus.SystemBus()
        self.uuid = "1101"
        self.opts = {
            "Name": "Mopidy SPP",
            "Service": "6e08ec37-60ec-4167-945e-9dd781ba6e1a",
            "Channel": dbus.UInt16(channel),
            "AutoConnect": False,
            "Role": "server",
        }

        self.manager = dbus.Interface(
            self.bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1")

    def register(self):
        try:
            self.manager.RegisterProfile(
                self.profile_path, self.uuid, self.opts,
            )
        except dbus.exceptions.DBusException:
            log.exception('failed to register profile')
            return False

        return True

    def unregister(self):
        try:
            self.manager.UnregisterProfile(self.profile_path)
        except dbus.exceptions.DBusException:
            log.exception('failed to unregister profile')


class ConnectionInfo(object):
    def __init__(self, fd):
        self.fd = fd
        self.msg_len = None


class BluetoothServer(dbus.service.Object):
    def __init__(self, core, *args, **kwargs):
        super(BluetoothServer, self).__init__(*args, **kwargs)
        self.jsonrpc = make_jsonrpc_wrapper(core)
        self._connections_by_path = collections.defaultdict(list)

    @dbus.service.method('org.bluez.Profile1',
                         in_signature='o',
                         out_signature='')
    def RequestDisconnection(self, path):
        self.disconnect(path)

    def disconnect(self, path):
        log.info('disconnecting: %s', path)
        for info in self._connections_by_path[path]:
            os.close(info.fd)
            del info.fd
        del self._connections_by_path[path]

    @dbus.service.method(
        "org.bluez.Profile1", in_signature="oha{sv}", out_signature="",
    )
    def NewConnection(self, path, fd, properties):
        log.info('NewConnection: %s', path)

        fd = fd.take()
        info = ConnectionInfo(fd=fd)
        self._connections_by_path[path].append(info)

        gi.repository.GObject.io_add_watch(
            fd,
            gi.repository.GObject.PRIORITY_DEFAULT,  # condition
            gi.repository.GObject.IO_IN | gi.repository.GObject.IO_PRI,
            self.read_cb,
        )

    def read_cb(self, fd, conditions):
        log.debug('--> #%s: reading header' % fd)
        data = os.read(fd, 4)
        size, = struct.unpack('!I', data)
        log.debug('--> #%s: reading %s bytes' % (fd, size))

        data = os.read(fd, size)

        response = self.jsonrpc.handle_json(data)
        if response:
            self.write_cb(fd, response)

        return True

    def broadcast(self, value):
        items = list(self._connections_by_path.items())
        for path, infos in items:
            try:
                for info in infos:
                    self.write_cb(info.fd, value)
            except:
                log.warning(
                    "Failed to write to %s, disconnecting", path,
                    exc_info=True,
                )
                self.disconnect(path)

    def write_cb(self, fd, value):
        data = value.encode('utf-8')
        buf = io.BytesIO()
        buf.write(to_msg_size(data))
        buf.write(data)

        data = buf.getvalue()
        log.debug('<-- #%s: sending %s bytes' % (fd, len(data)))
        os.write(fd, data)
        return True


def to_msg_size(data):
    count = len(data)
    return struct.pack('!I', count)
