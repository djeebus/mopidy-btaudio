import collections
import dbus
import functools
import logging
import pykka

from mopidy.audio.constants import PlaybackState
from mopidy.core import CoreListener
from mopidy.core.actor import Core

from mopidy_btaudio.agent import BlueAgent

logger = logging.getLogger('mopidy-btaudio')


class BtAudioController(pykka.ThreadingActor, CoreListener):
    def __init__(self, config, core):
        pykka.ThreadingActor.__init__(self)

        self.adapters = {}
        self._devices_connected = set()

        self.config = config
        self.core = core  # type: Core

        self.agent = None  # type: BlueAgent
        self.bus = None  # type: dbus.SystemBus

        self._added_handlers = {
            'org.bluez.Adapter1': self.on_adapter_added,
            'org.bluez.Device1': self.on_device_added,
            'org.bluez.MediaPlayer1': self.on_media_player_added,
        }

        self._changed_handlers = {
            'org.bluez.Adapter1': self.on_adapter_changed,
            'org.bluez.Device1': self.on_device_changed,
            'org.bluez.MediaPlayer1': self.on_media_player_changed,
        }

        self._removed_handlers = {
            'org.bluez.Adapter1': self.on_adapter_removed,
            'org.bluez.Device1': self.on_device_removed,
            'org.bluez.MediaPlayer1': self.on_media_player_removed,
        }

    def _initialize_dbus(self):
        bus = dbus.SystemBus()
        self.bus = bus

        root = bus.get_object('org.bluez', '/')
        object_manager = dbus.Interface(
            root, 'org.freedesktop.DBus.ObjectManager',
        )

        managed_objects = object_manager.GetManagedObjects()
        for path, interfaces in managed_objects.items():
            self.on_interfaces_added(path, interfaces)

        object_manager.connect_to_signal(
            'InterfacesAdded', self.on_interfaces_added,
        )

        object_manager.connect_to_signal(
            'InterfacesRemoved', self.on_interfaces_removed,
        )

    def on_interfaces_added(self, path, interface_names):
        found = False
        dbus_ob = self.bus.get_object('org.bluez', path)
        props_interface = dbus.Interface(
            dbus_ob, 'org.freedesktop.DBus.Properties',
        )
        for interface_name in interface_names:
            handler = self._added_handlers.get(interface_name)
            if handler:
                adapter = dbus.Interface(dbus_ob, interface_name)
                adapter_props = props_interface.GetAll(interface_name)
                handler(adapter, adapter_props)
                found = True
        if found:
            props_interface.connect_to_signal(
                'PropertiesChanged',
                functools.partial(self.on_properties_changed, dbus_ob),
            )

    def on_properties_changed(
        self, dbus_ob, interface, changed_props, invalid_props,
    ):
        handler = self._changed_handlers.get(interface)
        handler and handler(dbus_ob, changed_props, invalid_props)

    def on_interfaces_removed(self, path, interfaces):
        for interface in interfaces:
            handler = self._removed_handlers.get(interface)
            handler and handler(path)

    def on_device_added(self, interface, props):
        logger.info('device added: %s', interface.object_path)
        connected = props.get('Connected')
        if connected:
            path = str(interface.object_path)
            self._add_connected_device(path)

    def on_device_changed(self, dbus_ob, changed_props, invalid_props):
        logger.info('adapter changed: %s', changed_props)
        if 'Connected' not in changed_props:
            return

        connected = changed_props['Connected']
        path = str(dbus_ob.object_path)
        if connected:
            self._add_connected_device(path)
        else:
            self._remove_connected_device(path)

    def on_device_removed(self, path):
        logger.info('device removed: %s', path)
        self._remove_connected_device(path)

    def _add_connected_device(self, path):
        logger.info('device connected: %s', path)
        self._devices_connected.add(path)
        self._connections_updated()

    def _remove_connected_device(self, path):
        if path in self._devices_connected:
            logger.info('device disconnected: %s', path)
            self._devices_connected.remove(path)
            self._connections_updated()

    def _connections_updated(self):
        if self._devices_connected:
            self._set_discoverable(False)
        else:
            self._set_discoverable(True)

    def _set_discoverable(self, enable):
        for path, adapter in self.adapters.items():
            discoverable = getattr(adapter, 'Discoverable')
            if discoverable == enable:
                logger.info('discoverable already set to %s [%s]',
                            enable, path)
                continue

            logger.info('setting discoverable to %s', enable)
            setattr(adapter, 'Discoverable', discoverable)

    def on_media_player_added(self, interface, props):
        logger.info('media player added')
        media_player_status = props.get('Status')
        if media_player_status:
            self._on_bt_media_player_state(
                interface.object_path, media_player_status,
            )

    def on_media_player_changed(self, dbus_ob, changed_props, invalid_props):
        bt_status = changed_props.get('Status')
        if bt_status:
            logger.info('media player status changed')
            self._on_bt_media_player_state(dbus_ob.object_path, bt_status)

    def on_media_player_removed(self, path):
        logger.info('removing media player')
        self._on_bt_media_player_state(path, 'stopped')

    _bt_is_playing = set()
    _mopidy_was_playing = False

    def _on_bt_media_player_state(self, path, state):
        logger.info('media player %s = %s', path, state)

        if state == 'playing':
            self._bt_is_playing.add(path)

        if state in ['paused', 'stopped', 'error']:
            path = str(path)
            if path in self._bt_is_playing:
                self._bt_is_playing.remove(path)

        self.process_state()

    def process_state(self):
        if self._bt_is_playing:
            # pause mopidy, if necessary
            self._pause_mopidy()
        else:
            # mopidy resume, if necessary
            self._resume_mopidy()

    def _pause_mopidy(self):
        mopidy_status = self.core.playback.get_state().get()
        if mopidy_status == PlaybackState.PLAYING:
            logger.info('pausing playback')
            self._mopidy_was_playing = True
            self.core.playback.pause()

    def _resume_mopidy(self):
        if self._mopidy_was_playing:
            logger.info('resuming playback')
            self.core.playback.play()
            self._mopidy_was_playing = False

    def on_adapter_added(self, adapter, props):
        logger.info('initializing "%s"' % adapter.object_path)
        setattr(adapter, 'Name', self.config['btaudio']['name'])
        setattr(adapter, 'Powered', True)
        setattr(adapter, 'Discoverable', True)
        logger.info('initialized "%s"' % adapter.object_path)

        self.adapters[adapter.object_path] = adapter

    def on_adapter_changed(self, dbus_ob, changed_props, invalid_props):
        logger.info("adapter changed")

    def on_adapter_removed(self, path):
        logger.info('removing adapter')
        if path in self.adapters:
            del self.adapters[path]

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

    def playback_state_changed(self, old_state, new_state):
        self.process_state()
