import base64
import collections
import dbus
import dbus.exceptions
import dbus.service
import functools
import gi.repository
import io
import json
import logging
import os
import pykka
import struct
import threading
import time

from mopidy.core import Core
from mopidy.core import CoreListener
from mopidy.http.handlers import make_jsonrpc_wrapper
from mopidy.internal.path import get_or_create_dir
from mopidy.models.serialize import ModelJSONEncoder

log = logging.getLogger(__name__)


def report_exceptions(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except:
            log.exception("trapped exception")

    return wrapper


class BtRpcServer(pykka.ThreadingActor, CoreListener):
    @classmethod
    def get_data_dir(cls, config):
        """Get or create data directory for the extension.

        Use this directory to store data that should be persistent.

        :param config: the Mopidy config object
        :returns: string
        """
        data_dir_path = bytes(os.path.join(config['core']['data_dir'],
                                           'local-images'))
        get_or_create_dir(data_dir_path)
        return data_dir_path

    def __init__(self, config, core):
        pykka.ThreadingActor.__init__(self)

        self.config = config
        self.core = core  # type: Core

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._mainloop = gi.repository.GObject.MainLoop()

        if config['local-images']['image_dir']:
            image_dir = config['local-images']['image_dir']
        else:
            image_dir = self.get_data_dir(config)

        self._spp = SerialPort(1)
        self._server = BluetoothServer(
            core,
            image_dir,
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

    @report_exceptions
    def on_start(self):
        self._thread.start()

    @report_exceptions
    def on_stop(self):
        self.shutdown()
        self._thread.join(1)

    @report_exceptions
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
        except:
            log.exception('failed to register profile')
            return False

        return True

    def unregister(self):
        try:
            self.manager.UnregisterProfile(self.profile_path)
        except:
            log.exception('failed to unregister profile')


class ConnectionInfo(object):
    def __init__(self, fd):
        self.fd = fd
        self.msg_len = None
        self.write_lock = threading.Lock()


class BtRpc:
    def __init__(self, image_dir):
        self.image_dir = image_dir

    def get_image_data(self, uri):
        uri = uri.lstrip('/images/')
        path = os.path.join(self.image_dir, uri)
        if not os.path.exists(path):
            return

        with open(path, 'rb') as fp:
            data = fp.read()

        return base64.b64encode(data)


class BluetoothServer(dbus.service.Object):
    def __init__(self, core, image_dir, *args, **kwargs):
        super(BluetoothServer, self).__init__(*args, **kwargs)
        self.jsonrpc = make_jsonrpc_wrapper(core)
        self.jsonrpc.objects['btrpc'] = BtRpc(image_dir)
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
        del self._connections_by_path[path]

    @dbus.service.method(
        "org.bluez.Profile1", in_signature="oha{sv}", out_signature="",
    )
    def NewConnection(self, path, fd, properties):
        fd = fd.take()

        log.info('NewConnection: %s (#%s)', path, fd)

        info = ConnectionInfo(fd=fd)
        self._connections_by_path[path].append(info)

        gi.repository.GObject.io_add_watch(
            fd,
            gi.repository.GObject.PRIORITY_DEFAULT,  # condition
            gi.repository.GObject.IO_IN | gi.repository.GObject.IO_PRI,
            functools.partial(self.read_cb, path),
        )

    def read_cb(self, path, fd, conditions):
        try:
            log.debug('--> #%s: reading header' % fd)
            data = _io_retry(os.read, fd, 4)

            size, = struct.unpack('!I', data)
            log.debug('--> #%s: reading %s bytes' % (fd, size))

            data = _io_retry(os.read, fd, size)
            log.debug('--> #%s: %s' % (fd, data))
        except:
            log.exception('--> #%s: error reading, closing' % fd)
            self.disconnect(path)
            return False

        response = self.jsonrpc.handle_json(data)

        if response:
            self.write_cb(path, fd, response)

        return True

    def broadcast(self, value):
        log.info('broadcasting %s' % (value))
        items = list(self._connections_by_path.items())
        for path, infos in items:
            for info in infos:
                self.write_cb(path, info.fd, value)

    def write_cb(self, path, fd, value):
        infos = self._connections_by_path.get(path, [])
        for info in infos:
            if info.fd == fd:
                break
        else:
            log.warning('--> #%s: no info, bailing' % fd)
            return

        try:
            info.write_lock.acquire()
            data = value.encode('utf-8')
            buf = io.BytesIO()
            buf.write(to_msg_size(data))
            buf.write(data)

            remaining = buf.getvalue()

            while remaining:
                try:
                    log.debug('<-- #%s: sending %s bytes' % (fd, len(remaining)))
                    written = _io_retry(os.write, fd, remaining)
                    log.debug('<-- #%s: sent %s bytes' % (fd, written))
                    remaining = remaining[written:]
                except:
                    log.exception('<-- #%s: failed to write, closing socket' % fd)
                    self.disconnect(path)
                    return
        finally:
            info.write_lock.release()


def _io_retry(func, *args):
    while True:
        try:
            return func(*args)
        except OSError as e:
            if e.errno == 11:
                time.sleep(.005)  # don't spin
                continue

            raise


def to_msg_size(data):
    count = len(data)
    return struct.pack('!I', count)
