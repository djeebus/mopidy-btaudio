import collections
import dbus
import logging
import pykka

from mopidy.core import CoreListener
from mopidy.core.actor import Core

from mopidy_btaudio.agent import BlueAgent

logger = logging.getLogger('mopidy-btaudio')


class BluetoothAdapter:
    def __init__(self, path, adapter):
        self.path = path
        self.adapter = adapter


class BluetoothController:
    def __init__(self):
        self._added_handlers = collections.defaultdict(list)
        self._interface_map = collections.defaultdict(list)

    def add_on_interface_added_handler(self, interface, handler):
        self._interface_map[interface].append(handler)

    def _add_object(self, path, interfaces):
        for interface in interfaces:
            self._interface_map[interface].append(path)

            handler = self._added_handlers.get(interface)
            handler and handler(path)

    def _remove_object(self, path, interfaces):
        for interface in interfaces:
            self._interface_map[interface].remove(path)

    adapter_name = 'org.bluez.Adapter1'

    @property
    def adapters(self):
        dbus_objects = (
            self._bus.get_object('org.bluez', path)
            for path in self._interface_map.get(self.adapter_name)
        )

        return [
            dbus.Interface(dbus_object, self.adapter_name)
            for dbus_object in dbus_objects
        ]


class BtAudioController(pykka.ThreadingActor, CoreListener):
    def __init__(self, config, core):
        pykka.ThreadingActor.__init__(self)

        self.adapters = {}

        self.config = config
        self.core = core  # type: Core

        self.agent = None  # type: BlueAgent
        self.bus = None  # type: dbus.SystemBus

    def _initialize_dbus(self):
        bus = dbus.SystemBus()
        self.bus = bus

        root = bus.get_object('org.bluez', '/')
        object_manager = dbus.Interface(
            root, 'org.freedesktop.DBus.ObjectManager',
        )

        managed_objects = object_manager.GetManagedObjects()
        for path, interfaces in managed_objects.items():
            self._dbus_object_added(path, interfaces)

        object_manager.connect_to_signal(
            'InterfacesAdded', self._dbus_object_added,
        )

        object_manager.connect_to_signal(
            'InterfacesRemoved', self._dbus_object_removed,
        )

    def _dbus_object_added(self, path, interfaces):
        handlers = {
            'org.bluez.Adapter1': self.on_adapter_added,
            'org.bluez.MediaPlayer1': self.on_media_player_added,
            'org.bluez.MediaTransport1': self.on_audio_transport_added,
        }

        found = False
        dbus_ob = self.bus.get_object('org.bluez', path)
        for interface in interfaces:
            handler = handlers.get(interface)
            if handler:
                adapter = dbus.Interface(dbus_ob, interface)
                handler(adapter)
                found = True
        if found:
            adapter = dbus.Interface(
                dbus_ob, 'org.freedesktop.DBus.Properties',
            )
            

    def on_media_player_added(self, interface):
        logger.info('added media player')

    def on_audio_transport_added(self, interface):
        logger.info('added audio transport')

    def on_adapter_added(self, adapter):
        logger.info('initializing "%s"' % adapter.object_path)
        setattr(adapter, 'Name', self.config['btaudio']['name'])
        setattr(adapter, 'Powered', True)
        setattr(adapter, 'Discoverable', True)
        logger.info('initialized "%s"' % adapter.object_path)

        self.adapters[adapter.object_path] = adapter

    def _dbus_object_removed(self, path, interfaces):
        handlers = {
            'org.bluez.Adapter1': self.on_adapter_removed,
            'org.bluez.MediaPlayer1': self.on_media_player_removed,
            'org.bluez.MediaTransport1': self.on_audio_transport_removed,
        }

        for interface in interfaces:
            handler = handlers.get(interface)
            handler and handler(path)

    def on_adapter_removed(self, path):
        logger.info('removing adapter')
        if path in self.adapters:
            del self.adapters[path]

    def on_media_player_removed(self, path):
        self.on
        logger.info('removing media player')

    def on_audio_transport_removed(self, path):
        logger.info('removing audio transport')

    def on_start(self):
        self._initialize_dbus()

        agent = BlueAgent(self.config['btaudio']['pin'])
        agent.register_as_default()

        self.agent = agent

    def on_stop(self):
        for adapter in self.adapters.values():
            setattr(adapter, 'Discoverable', False)
            setattr(adapter, 'Powered', False)

        self.adapters.clear()
