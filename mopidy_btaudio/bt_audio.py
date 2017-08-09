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
            self.on_interfaces_added(path, interfaces)

        object_manager.connect_to_signal(
            'InterfacesAdded', self.on_interfaces_added,
        )

        object_manager.connect_to_signal(
            'InterfacesRemoved', self.on_interfaces_removed,
        )

    def on_interfaces_added(self, path, interface_names):
        handlers = {
            'org.bluez.Adapter1': self.on_adapter_added,
            'org.bluez.MediaPlayer1': self.on_media_player_added,
            'org.bluez.MediaTransport1': self.on_media_transport_added,
        }

        found = False
        dbus_ob = self.bus.get_object('org.bluez', path)
        props_interface = dbus.Interface(
            dbus_ob, 'org.freedesktop.DBus.Properties',
        )
        for interface_name in interface_names:
            handler = handlers.get(interface_name)
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
        handlers = {
            'org.bluez.Adapter1': self.on_adapter_changed,
            'org.bluez.MediaPlayer1': self.on_media_player_changed,
            'org.bluez.MediaTransport1': self.on_media_transport_changed,
        }

        handler = handlers.get(interface)
        handler and handler(dbus_ob, changed_props, invalid_props)

    def on_interfaces_removed(self, path, interfaces):
        handlers = {
            'org.bluez.Adapter1': self.on_adapter_removed,
            'org.bluez.MediaPlayer1': self.on_media_player_removed,
            'org.bluez.MediaTransport1': self.on_media_transport_removed,
        }

        for interface in interfaces:
            handler = handlers.get(interface)
            handler and handler(path)

    def on_media_player_added(self, interface, props):
        media_player_status = props.get('Status')
        if media_player_status:
            self._on_bt_media_player_state(media_player_status)

    def on_media_player_changed(self, dbus_ob, changed_props, invalid_props):
        bt_status = changed_props.get('Status')
        if bt_status:
            self._on_bt_media_player_state(bt_status)

    _bt_is_playing = None
    _mopidy_was_playing = None

    def _on_bt_media_player_state(self, state):
        if state == 'playing':
            self._bt_is_playing = True

            mopidy_status = self.core.playback.get_state().get()
            if mopidy_status == PlaybackState.PLAYING:
                self._mopidy_was_playing = True
                self.core.playback.pause()
            return

        if state in ['paused', 'stopped', 'error']:
            if self._mopidy_was_playing:
                self._bt_is_playing = False
                self.core.playback.play()
                self._mopidy_was_playing = None
            return

    def on_media_player_removed(self, path):
        logger.info('removing media player')
        self._on_bt_media_player_state('stopped')

    def on_media_transport_added(self, interface, props):
        logger.info('added audio transport')

    def on_media_transport_changed(
        self, dbus_ob, changed_props, invalid_props,
    ):
        logger.info("media transport changed: \nchanged: %s\ninvalid: %s"
                    % (changed_props, invalid_props))

    def on_media_transport_removed(self, path):
        logger.info('removing audio transport')

    def on_adapter_added(self, adapter, props):
        logger.info('initializing "%s"' % adapter.object_path)
        setattr(adapter, 'Name', self.config['btaudio']['name'])
        setattr(adapter, 'Powered', True)
        setattr(adapter, 'Discoverable', True)
        logger.info('initialized "%s"' % adapter.object_path)

        self.adapters[adapter.object_path] = adapter

    def on_adapter_changed(self, dbus_ob, changed_props, invalid_props):
        logger.info("media transport changed: \nchanged: %s\ninvalid: %s"
                    % (changed_props, invalid_props))

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
        if new_state == PlaybackState.PLAYING:
            if self._bt_is_playing:
                logger.info('pausing mopidy, bluetooth is active')
                self._mopidy_was_playing = True
                self.core.playback.pause()
